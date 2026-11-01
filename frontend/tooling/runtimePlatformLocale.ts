import { parseExpression, parse as parseScript } from '@babel/parser'
import type { Plugin } from 'vite'
import { parse } from '@vue/compiler-sfc'
import { parse as parseTemplate, NodeTypes } from '@vue/compiler-dom'

/** Translate only authored template UI, never interpolated records or binding values. */
export function localizeOutputExpression(expression: string, helper = '$platformText'): string {
  const edits: {start: number; end: number; text: string}[] = []
  const walk = (node: any, parent?: any, key?: string) => {
    if (!node || typeof node !== 'object') return
    if (node.type === 'TemplateElement' && /[\u4e00-\u9fff]/.test(node.value.cooked || '')) {
      edits.push({start:node.start,end:node.end,text:'${'+helper+'('+JSON.stringify(node.value.cooked)+')}'})
      return
    }
    if (node.type === 'StringLiteral' && /[\u4e00-\u9fff]/.test(node.value)) {
      if (parent && ((parent.type === 'MemberExpression' || parent.type === 'OptionalMemberExpression') && key === 'property' || parent.type === 'ObjectProperty' && key === 'key' || /BinaryExpression|CallExpression|NewExpression/.test(parent.type))) return
      edits.push({start: node.start, end: node.end, text: `${helper}(${expression.slice(node.start,node.end)})`})
      return
    }
    for (const [field, child] of Object.entries(node)) {
      if (['loc','extra','start','end','comments'].includes(field)) continue
      if (Array.isArray(child)) child.forEach(value => walk(value,node,field))
      else if (child && typeof child === 'object') walk(child,node,field)
    }
  }
  try { walk(parseExpression(expression, {plugins:['typescript']})) } catch { return expression }
  for (const edit of edits.sort((a,b)=>b.start-a.start)) expression=expression.slice(0,edit.start)+edit.text+expression.slice(edit.end)
  return expression
}
export function runtimePlatformLocale(): Plugin {
  return {
    name: 'runtime-platform-locale', enforce: 'pre',
    transform(source, id) {
      if (process.env.VITE_EDITION === 'mail_en' || !/\/src\/.*\.vue$/.test(id)) return
      const descriptor = parse(source).descriptor
      const template = descriptor.template
      if (!template) return
      const edits: { start: number; end: number; text: string }[] = []
      const offset = template.loc.start.offset
      const expressionEdit = (expression: any, attribute = false) => {
        if (!expression || expression.type !== NodeTypes.SIMPLE_EXPRESSION) return
        const displayMap = /^(?:labels|reviewLabels)\[/.test(expression.content) || /^(?:statusLabel|roleLabel)$/.test(expression.content)
        const text = displayMap ? `$platformText(${expression.content})` : localizeOutputExpression(expression.content)
        if(text !== expression.content) edits.push({start:offset+expression.loc.start.offset,end:offset+expression.loc.end.offset,text:attribute ? text.replaceAll('"', '&quot;').replaceAll("'", '&#39;') : text})
      }
      const visit = (node: any) => {
        if(node.type === NodeTypes.INTERPOLATION) expressionEdit(node.content)
        if (node.type === NodeTypes.TEXT && /[\u4e00-\u9fff]/.test(node.content)) {
          edits.push({ start: offset + node.loc.start.offset, end: offset + node.loc.end.offset, text: `{{ $platformText(${JSON.stringify(node.content)}) }}` })
        }
        if (node.type === NodeTypes.ELEMENT) {
          for (const prop of node.props) {
            if(prop.type === NodeTypes.DIRECTIVE && prop.name === 'bind' && prop.arg && /^(label|title|placeholder|aria-label|description|empty-text|content|confirm-button-text|cancel-button-text)$/.test(prop.arg.content)) expressionEdit(prop.exp, true)
            if (prop.type === NodeTypes.ATTRIBUTE && /^(label|title|placeholder|aria-label|description|empty-text|content|confirm-button-text|cancel-button-text)$/.test(prop.name) && prop.value && /[\u4e00-\u9fff]/.test(prop.value.content)) {
              edits.push({ start: offset + prop.loc.start.offset, end: offset + prop.loc.end.offset, text: `:${prop.name}='${`$platformText(${JSON.stringify(prop.value.content)})`.replaceAll("'", '&#39;')}'` })
            }
          }
        }
        for (const child of node.children || []) visit(child)
      }
      visit(parseTemplate(template.content))
      for (const script of [descriptor.script, descriptor.scriptSetup]) {
        if (!script) continue
        const ast = parseScript(script.content, {sourceType:'module', plugins:['typescript']})
        let used = false
        const visitScript = (node: any, ui = false, parent?: any, field?: string) => {
          if (!node || typeof node !== 'object') return
          if(node.type === 'ObjectProperty' && !node.computed && ['label','title','description','placeholder'].includes(node.key?.name || node.key?.value) && node.value?.type === 'StringLiteral' && /[\u4e00-\u9fff]/.test(node.value.value)) {
            const key = script.content.slice(node.key.start,node.key.end)
            edits.push({start:script.loc.start.offset+node.start,end:script.loc.start.offset+node.end,text:`get ${key}() { return __platformText(${JSON.stringify(node.value.value)}) }`})
            used=true; return
          }
          const displayFunction = node.type === 'VariableDeclarator' && /^(?:statusText|attemptText|reviewLabel|amountText|errorText|emptyPaymentText|emptyText)$/.test(node.id?.name)
          const call = node.type === 'CallExpression' && node.callee?.type === 'MemberExpression' && ['ElMessage','ElMessageBox'].includes(node.callee.object?.name)
          if(node.type === 'TemplateElement' && ui && /[\u4e00-\u9fff]/.test(node.value.cooked || '')) {
            edits.push({start:script.loc.start.offset+node.start,end:script.loc.start.offset+node.end,text:'${__platformText('+JSON.stringify(node.value.cooked)+')}'})
            used=true; return
          }
          if (node.type === 'StringLiteral' && parent && (/BinaryExpression/.test(parent.type) || /MemberExpression/.test(parent.type) && field === 'property')) return
          if (node.type === 'StringLiteral' && ui && /[\u4e00-\u9fff]/.test(node.value)) {
            edits.push({start:script.loc.start.offset+node.start,end:script.loc.start.offset+node.end,text:`__platformText(${script.content.slice(node.start,node.end)})`})
            used=true
            return
          }
          for(const [key,child] of Object.entries(node)) {
            if(['loc','extra','start','end','comments','key'].includes(key)) continue
            if(Array.isArray(child)) child.forEach(value=>visitScript(value,ui || call && key==='arguments' || displayFunction && key==='init',node,key))
            else if(child && typeof child === 'object') visitScript(child,ui || call && key==='arguments' || displayFunction && key==='init',node,key)
          }
        }
        visitScript(ast)
        if(used) edits.push({start:script.loc.start.offset,end:script.loc.start.offset,text:`\nimport { platformText as __platformText } from '/src/platformLocale'\n`})
      }
      for (const edit of edits.sort((a,b) => b.start-a.start)) source = source.slice(0,edit.start)+edit.text+source.slice(edit.end)
      return { code: source, map: null }
    },
  }
}
