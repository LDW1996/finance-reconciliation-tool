import { ElMessageBox, type ElMessageBoxOptions } from 'element-plus'

const customClass = 'action-dialog'

export const actionDialog = {
  confirm(message: string, title: string, options: ElMessageBoxOptions = {}) {
    return ElMessageBox.confirm(message, title, { ...options, customClass })
  },
  alert(message: string, title: string, options: ElMessageBoxOptions = {}) {
    return ElMessageBox.alert(message, title, { ...options, customClass })
  },
}
