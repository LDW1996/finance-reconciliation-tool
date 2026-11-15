<template>
  <main class="unlock-shell">
    <div v-if="view === 'unlock'" class="unlock-workspace ds-scope">
      <template v-if="taskId">
        <section class="unlock-card task-progress" aria-labelledby="unlock-progress-title">
          <div class="unlock-card-head">
            <div class="progress-heading">
              <h2 id="unlock-progress-title" ref="progressHeading" tabindex="-1">执行进度</h2>
              <StatusBadge v-if="taskQueued" tone="processing" label="已排队" />
              <StatusBadge v-else-if="paused" tone="warning" label="已暂停" />
              <StatusBadge v-else-if="taskState === 'running'" tone="processing" label="执行中" />
              <StatusBadge v-else-if="taskState === 'closing'" tone="processing" label="收尾中" />
              <StatusBadge v-else :tone="attentionCount ? 'warning' : 'success'" label="任务已结束" />
            </div>
            <div class="unlock-actions">
              <template v-if="taskState === 'running' || paused">
                <el-button :disabled="controlling" :icon="paused ? VideoPlay : VideoPause" @click="controlTask(paused ? 'resume' : 'pause')">
                  {{ paused ? '继续剩余项' : '暂停后续项' }}
                </el-button>
                  <el-button plain type="danger" :disabled="controlling" @click="cancelRemaining">取消剩余项</el-button>
              </template>
              <el-button v-else :icon="Plus" :disabled="busy || controlling || !taskSyncReady" :loading="resetting" @click="newTask">新建任务</el-button>
            </div>
          </div>
          <div class="progress-count"><strong>{{ done }}</strong><span>/ {{ rows.length }}</span><small>已处理</small></div>
          <el-progress :percentage="percentage" :show-text="false" :stroke-width="8" color="var(--accent)" />
          <div class="progress-line">
            <p class="progress-caption" aria-live="polite">
              <template v-if="paused">已暂停，继续将跳过待核实项。</template>
              <template v-else-if="currentRow">{{ platformText('正在处理') }} <strong>{{ currentRow.bill }}</strong></template>
              <template v-else-if="taskQueued">上一批收尾完成后，本批会自动开始。</template>
              <template v-else-if="!taskNotice && waitingHint">{{ platformText('正在等待平台切换或上一个任务收尾（关闭浏览器），稍候几秒即可开始；无需重复提交') }}</template>
            </p>
            <div class="progress-meta">
              <span>{{ taskAccountName }}</span>
              <span>{{ taskVerifyAfter ? '标准模式（复查）' : '快速模式（不复查）' }}</span>
            </div>
          </div>
          <p v-if="taskNotice && !taskQueued" class="progress-notice" role="status">{{ taskNotice }}</p>
        </section>

        <section class="unlock-card task-results" aria-labelledby="unlock-results-title">
          <div class="unlock-card-head">
            <h2 id="unlock-results-title">执行结果</h2>
            <div class="unlock-actions">
              <el-button :icon="Download" :disabled="!rows.length" @click="exportResults()">导出结果</el-button>
            </div>
          </div>
          <div class="result-filters" role="group" aria-label="筛选结果">
            <button v-for="item in resultFilters" :key="item.key" type="button" :aria-pressed="filter === item.key" @click="filter = item.key">
              {{ item.label }} <span>{{ item.count }}</span>
            </button>
          </div>
          <el-table :data="filteredRows" max-height="520" empty-text="没有符合条件的结果" :row-class-name="rowClassName">
            <el-table-column label="#" width="44">
              <template #default="{ row }">{{ row.evidenceIndex + 1 }}</template>
            </el-table-column>
            <el-table-column prop="bill" label="提单号" min-width="148">
              <template #default="{ row }"><span class="bill-no">{{ row.bill }}</span></template>
            </el-table-column>
            <el-table-column label="结果" min-width="140">
              <template #default="{ row }"><StatusBadge :tone="statusStyle(row.status)" :label="statusText(row.status, row.detail)" /></template>
            </el-table-column>
            <el-table-column label="类型" min-width="106">
              <template #default="{ row }">{{ row.kind === 'fee' ? '费用解锁' : '订单解锁' }}</template>
            </el-table-column>
            <el-table-column label="平台响应" min-width="110">
              <template #default="{ row }">{{ platformResponseText(row.detail) }}</template>
            </el-table-column>
            <el-table-column label="锁状态复查" min-width="120">
              <template #default="{ row }">{{ row.detail?.before?.lock || '—' }} → {{ lockRecheckText(row.detail) }}</template>
            </el-table-column>
            <el-table-column label="详情" min-width="106">
              <template #default="{ row }">
                <el-button v-if="row.status !== 'waiting' && row.status !== 'processing'" link type="primary" @click="rowDetailIndex = row.evidenceIndex">
                  {{ row.status === 'attention' ? '查看原因' : '查看详情' }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <footer class="result-footer">
            <span v-if="busy">结果逐笔更新</span>
            <el-button link type="primary" :icon="RefreshRight" @click="refreshTask">刷新结果</el-button>
          </footer>
        </section>

        <section class="unlock-card task-input-summary">
          <div>
            <strong>任务输入</strong>
            <span>{{ taskAccountName }}<template v-if="taskFallbackName"> → {{ taskFallbackName }}</template></span>
          </div>
          <el-button text type="primary" :aria-expanded="!inputFolded" aria-controls="unlock-draft" @click="inputFolded = !inputFolded">
            {{ inputFolded ? '展开输入' : '收起输入' }}
          </el-button>
        </section>
      </template>

      <el-alert v-if="pollError" :title="pollError" type="warning" :closable="false" role="alert" />
      <div id="unlock-draft" v-show="!taskId || !inputFolded" class="unlock-draft">
        <div class="unlock-draft-fields">
          <section class="unlock-card" aria-labelledby="unlock-bills-title">
            <div class="unlock-card-head"><h2 id="unlock-bills-title">{{ scope === 'voyage' ? '输入代表提单' : '输入提单号' }}</h2></div>
            <p class="unlock-help">{{ scope === 'voyage' ? '每行输入一个航次的代表提单，支持批量处理' : '每行一个提单号，也支持逗号、空格分隔' }}</p>
            <div class="bill-input-grid">
              <section v-for="kind in kinds" :key="kind.key" class="unlock-input-panel">
                <div class="bill-input-heading">
                  <label :for="`bills-${kind.key}`"><el-icon><Unlock /></el-icon>{{ kind.title }}</label>
                  <span class="count-pill">{{ parsed[kind.key].length }} 条</span>
                </div>
                <textarea :id="`bills-${kind.key}`" v-model="input[kind.key]" :disabled="busy" spellcheck="false" :placeholder="kind.placeholder" />
                <div class="input-caption">
                  <span>{{ duplicateCount(kind.key) ? `已合并 ${duplicateCount(kind.key)} 条重复单号` : '自动合并同类重复单号' }}</span>
                  <button type="button" :disabled="busy || !input[kind.key]" @click="input[kind.key] = ''">清空</button>
                </div>
                <div v-if="duplicates[kind.key].length" class="duplicate-bills" role="status">
                  <strong>重复单号（每个仅保留一次）</strong>
                  <ul><li v-for="item in duplicates[kind.key]" :key="item.bill"><code>{{ item.bill }}</code><span>出现 {{ item.count }} 次 · 重复 {{ item.count - 1 }} 次</span></li></ul>
                </div>
                <el-text v-if="inspections[kind.key].some(r => r.status === 'invalid')" type="danger">
                  {{ scope === 'voyage' ? '请修改格式异常的单号后再查询航次。' : '有格式异常的单号，请在核对清单中查看并返回修改。' }}
                </el-text>
              </section>
            </div>
            <div class="bill-input-footer">
              <span class="unlock-help">{{ total }} {{ platformText('项') }}</span>
              <el-button type="primary" :disabled="busy || !taskSyncReady || !reviewRows.length || !selectedAccount || (scope === 'voyage' && (!!invalidCount || total > 2000))" :icon="VideoPlay" :loading="previewLoading" @click="scope === 'voyage' ? queryVoyages() : openReview()">
                {{ scope === 'voyage' ? '查询航次并核对' : '校验单号并核对清单' }}
              </el-button>
            </div>
            <p v-if="!runtimeAvailable" class="demo-note">{{ runtimeMessage }}</p>
          </section>

          <details class="unlock-card execution-options">
            <summary>
              <strong><el-icon><Setting /></el-icon>执行选项</strong>
              <span>{{ verifyAfter ? '标准模式（复查）' : '快速模式（不复查）' }} · {{ screenshotPolicy === 'errors' ? '仅异常保存' : screenshotPolicy === 'all' ? '每笔保存' : '不保存' }}</span>
              <el-icon class="disclosure-icon"><ArrowDown /></el-icon>
            </summary>
            <div class="execution-options-fields">
              <div class="config-field">
                <div class="config-label">执行模式</div>
                <el-radio-group v-model="verifyAfter" :disabled="busy" aria-label="执行模式" class="unlock-options">
                  <el-radio-button :value="true">标准模式（复查）</el-radio-button>
                  <el-radio-button :value="false">快速模式（不复查）</el-radio-button>
                </el-radio-group>
              </div>
              <div class="config-field">
                <div class="config-label">截图方式</div>
                <el-radio-group v-model="screenshotPolicy" :disabled="busy" aria-label="截图方式" class="unlock-options">
                  <el-radio-button value="errors">仅异常保存</el-radio-button>
                  <el-radio-button value="all">每笔保存</el-radio-button>
                  <el-radio-button value="none">不保存</el-radio-button>
                </el-radio-group>
              </div>
            </div>
            <div class="execution-timing-row">
              <span class="config-label">{{ platformText('记录分阶段耗时') }}</span>
              <el-switch v-model="recordTimings" :disabled="busy" :aria-label="platformText('记录分阶段耗时')" />
            </div>
          </details>
        </div>

        <div class="unlock-draft-sidebar">
          <section class="unlock-card" aria-labelledby="unlock-settings-title">
            <div class="unlock-card-head"><h2 id="unlock-settings-title">任务设置</h2></div>
            <div class="config-row">
              <div class="config-field">
                <div class="config-label">首选账号</div>
                <el-select v-model="accountId" aria-label="首选账号" :disabled="busy" placeholder="选择账号">
                  <el-option v-for="a in accounts" :key="a.id" :label="a.name" :value="a.id" />
                </el-select>
                <button v-if="!accounts.length" type="button" class="config-link" @click="emit('navigate', 'accounts')">添加业务账号</button>
              </div>
              <div class="config-field">
                <div class="config-label">备用账号 <el-switch v-model="fallbackEnabled" :disabled="busy" aria-label="启用备用账号" /></div>
                <el-select v-model="selectedFallbackId" :disabled="busy || !fallbackEnabled || !fallbackCandidates.length" aria-label="备用账号" placeholder="没有可用备用账号">
                  <el-option v-for="a in fallbackCandidates" :key="a.id" :label="a.name" :value="a.id" />
                </el-select>
                <span class="config-hint">{{ !fallbackEnabled ? '未启用，仅使用首选账号' : fallbackCandidates.length ? '仅首选账号查无订单时使用' : '没有可用备用账号，仅使用首选账号' }}</span>
              </div>
              <div class="config-field">
                <div id="unlock-scope-label" class="config-label">解锁范围</div>
                <el-radio-group v-model="scope" :disabled="busy" aria-labelledby="unlock-scope-label" class="unlock-options scope-options">
                  <el-radio-button value="bill">按提单</el-radio-button>
                  <el-radio-button value="voyage">按航次</el-radio-button>
                </el-radio-group>
              </div>
            </div>
          </section>

          <aside v-if="scope === 'bill'" class="unlock-card task-guide" aria-labelledby="unlock-guide-title">
            <div class="unlock-card-head">
              <h2 id="unlock-guide-title">任务进度</h2><StatusBadge tone="neutral" label="待核对" />
            </div>
            <ol>
              <li><strong>校验单号</strong><span>检查格式，显示重复或异常单号。</span></li>
              <li><strong>确认清单</strong><span>核对账号、提单和大小锁类型。</span></li>
              <li><strong>执行并查看结果</strong><span>逐笔显示进度和待核实项。</span></li>
            </ol>
          </aside>

          <aside v-else class="unlock-card voyage-review" aria-labelledby="unlock-voyage-title">
            <div class="unlock-card-head"><h2 id="unlock-voyage-title" ref="voyageHeading" tabindex="-1">航次核对</h2></div>
            <p v-if="!preview" class="unlock-help">输入代表提单并查询后核对实际账号、船名航次及类型。</p>
            <template v-else>
              <el-alert v-for="(issue, index) in preview.issues" :key="index" :title="`${issue.bill} · ${issue.kind === 'fee' ? platformText('费用小锁') : platformText('订单大锁')} · ${issue.message}`" type="error" :closable="false" />
              <el-select v-if="preview.groups.length > 1" v-model="groupKey" aria-label="选择航次分组">
                <el-option v-for="g in preview.groups" :key="g.key" :value="g.key" :label="`${g.account_name} · ${g.ship}/${g.voyage} · ${g.kind === 'fee' ? platformText('费用小锁') : platformText('订单大锁')}`" />
              </el-select>
              <template v-if="group">
                <dl class="unlock-properties">
                  <dt>输入代表提单</dt><dd>{{ group.bills?.join('、') || group.bill }}</dd>
                  <dt>实际账号</dt><dd>{{ group.account_name }}</dd>
                  <dt>船名 / 航次</dt><dd>{{ group.ship || '—' }} / {{ group.voyage || '—' }}</dd>
                  <dt>类型</dt><dd>{{ group.kind === 'fee' ? '费用小锁' : '订单大锁' }}</dd>
                  <dt>影响提单数</dt><dd>平台未提供</dd>
                </dl>
                <details>
                  <summary>{{ platformText('查看查询匹配明细') }} · {{ group.records.length }} {{ platformText('条') }}</summary>
                  <el-table :data="group.records" max-height="260">
                    <el-table-column v-for="column in voyageColumns" :key="column.prop" :prop="column.prop" :label="column.label" min-width="116" />
                  </el-table>
                </details>
              </template>
            </template>
            <el-alert title="本次操作影响整个航次，不仅是输入提单。" type="warning" :closable="false" show-icon />
            <template v-if="preview">
              <el-alert v-if="expired" title="预览已过期，请重新查询。" type="warning" :closable="false" />
              <el-checkbox v-model="voyageAcknowledged" :disabled="!preview.can_execute || !!preview.issues.length || expired || busy">
                已核对全部分组，确认按航次执行
              </el-checkbox>
            </template>
            <el-button type="primary" :loading="submitting" :disabled="!canExecuteVoyage || busy || !taskSyncReady || !runtimeAvailable" @click="start">确认按航次解锁</el-button>
          </aside>
        </div>
      </div>
    </div>

    <div v-if="view === 'accounts'" class="accounts-workspace ds-scope">
      <section class="management-section ds-flush-table" aria-labelledby="accounts-title">
        <div class="accounts-card-header">
          <h2 id="accounts-title">{{ platformText('业务账号') }}</h2>
          <el-button type="primary" :icon="Plus" :disabled="busy" @click="openAccount()">添加账号</el-button>
        </div>
        <UnlockAccountsTable :accounts="accounts" :busy="busy" @edit="openAccount" @remove="removeAccount" />
      </section>
      <details class="management-section platform-address-settings">
        <summary>
          <strong>{{ platformText('平台地址') }}</strong>
          <el-icon class="disclosure-icon" aria-hidden="true"><ArrowDown /></el-icon>
        </summary>
        <el-form label-position="top" @submit.prevent class="platform-address-form">
          <el-form-item :label="platformText('平台地址')">
            <el-input v-model="platformAddress" placeholder="https://jk.sitc.com/ilog/" :disabled="addressBusy || !addressLoaded" maxlength="300" />
          </el-form-item>
          <el-form-item :label="platformText('登录测试账号')">
            <el-select v-model="testAccountId" :disabled="addressBusy" style="width: 100%">
              <el-option v-for="account in accounts.filter(item => item.hasPassword)" :key="account.id" :label="account.name" :value="account.id" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button :loading="testingAddress" :disabled="busy || savingAddress || !platformAddress.trim() || !testAccountId" @click="testPlatformAddress">{{ platformText('测试登录') }}</el-button>
            <el-button type="primary" :loading="savingAddress" :disabled="busy || testingAddress || !platformAddress.trim() || platformAddress === savedPlatformAddress" @click="savePlatformAddress">{{ platformText('保存') }}</el-button>
          </el-form-item>
        </el-form>
      </details>
    </div>

    <section v-if="view === 'history'" class="management-section ds-flush-table history-card" aria-labelledby="history-title">
      <header class="accounts-card-header">
        <h2 id="history-title">解锁历史</h2>
        <el-button :icon="RefreshRight" :loading="historyLoading" @click="loadHistory">刷新</el-button>
      </header>
      <el-table v-loading="historyLoading" :data="historyItems" empty-text="暂无历史任务">
        <el-table-column label="创建时间" min-width="170"><template #default="{ row }">{{ formatDate(row.createdAt) }}</template></el-table-column>
        <el-table-column prop="accountName" label="主账号" min-width="180"/>
        <el-table-column prop="fallbackAccountName" label="兜底账号" min-width="180"/>
        <el-table-column label="状态" width="120"><template #default="{ row }"><StatusBadge :tone="historyTone[row.state as Task['state']]" :label="{running:'执行中',paused:'已暂停',closing:'收尾中',completed:'已结束'}[row.state as Task['state']]" /></template></el-table-column>
        <el-table-column prop="total" label="总项数" width="80"/>
        <el-table-column prop="attention" label="待核实" width="80"/>
        <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link type="primary" @click="viewHistory(row.id)">查看</el-button></template></el-table-column>
      </el-table>
      <div v-if="historyTotal > 0" class="management-footer history-pagination"><el-pagination v-model:current-page="historyPage" :page-size="20" :total="historyTotal" layout="prev,pager,next,total" @current-change="loadHistory"/></div>
    </section>
    <el-dialog :model-value="!!detailRow" append-to-body class="bill-review-dialog" title="执行详情" width="min(560px, 94vw)" @close="rowDetailIndex = null">
      <template v-if="detailRow">
        <div class="detail-title">
          <strong class="bill-no">{{ detailRow.bill }}</strong>
          <StatusBadge :tone="statusStyle(detailRow.status)" :label="statusText(detailRow.status, detailRow.detail)" />
        </div>
        <p class="detail-message">{{ detailRow.detail?.status === 'voyage_platform_confirmed' ? cleanMessage(detailRow.detail.reason || '航次平台确认，全航次影响数量未提供') : detailRow.message }}</p>
        <dl class="unlock-properties">
          <dt>业务号</dt><dd>{{ detailRow.businessNo || detailRow.detail?.before?.booking || '—' }}</dd>
          <dt>实际使用账号</dt><dd>{{ detailRow.actualAccountName || '—' }}</dd>
          <template v-if="detailRow.attempts?.length">
            <dt>查询路径</dt>
            <dd><div v-for="(attempt, index) in detailRow.attempts" :key="index">{{ attempt.accountName }} · {{ attemptText(attempt.status) }}</div></dd>
          </template>
          <dt>平台响应</dt><dd>{{ platformResponseText(detailRow.detail) }}</dd>
          <dt>锁状态复查</dt><dd>{{ detailRow.detail?.before?.lock || '—' }} → {{ lockRecheckText(detailRow.detail) }}</dd>
          <dt>处理时间</dt><dd>{{ detailRow.time || '—' }}</dd>
          <dt>凭证</dt>
          <dd>
            <el-link v-if="detailRow.detail?.screenshot" :href="`/api/ilog/tasks/${encodeURIComponent(taskId)}/evidence/${detailRow.evidenceIndex}`" target="_blank" rel="noopener" type="primary">查看截图</el-link>
            <span v-else>{{ detailRow.detail?.screenshotExpired ? '已过期清理' : '—' }}</span>
          </dd>
          <template v-if="detailTimings.length">
            <dt>分阶段耗时</dt>
            <dd><div v-for="(timing, index) in detailTimings" :key="index">{{ timing.stage }} · {{ timing.seconds.toFixed(2) }} s</div></dd>
          </template>
        </dl>
      </template>
    </el-dialog>
    <el-dialog v-model="historyDetailOpen" append-to-body class="bill-review-dialog" title="历史任务详情" width="min(1100px,94vw)" top="4vh">
      <p>{{ historyTask?.accountName }} · {{ formatDate(historyTask?.createdAt) }} · {{ historyTask?.verify_after === false ? '快速模式（不复查）' : '标准模式（复查）' }}</p>
      <el-table :data="historyTask?.rows || []" max-height="480" empty-text="暂无记录">
        <el-table-column prop="bill" label="单号" width="185" fixed="left"/>
        <el-table-column label="结果" width="150" fixed="left"><template #default="{ row }">{{ statusText(row.status,row.detail) }}</template></el-table-column>
        <el-table-column label="业务号" min-width="160"><template #default="{row}">{{ row.businessNo || row.detail?.before?.booking || '—' }}</template></el-table-column>
        <el-table-column label="类型" width="100"><template #default="{ row }">{{ row.kind==='fee' ? '小锁' : '大锁' }}</template></el-table-column>
        <el-table-column prop="actualAccountName" label="实际账号" min-width="170"/>
        <el-table-column label="说明" min-width="240"><template #default="{ row }">{{ cleanMessage(row.detail?.status === 'voyage_platform_confirmed' ? row.detail?.reason || '航次平台确认，全航次影响数量未提供' : row.message) }}</template></el-table-column>
        <el-table-column label="截图" width="110"><template #default="{ row,$index }"><el-link v-if="row.detail?.screenshot" :href="`/api/ilog/tasks/${historyTask?.id}/evidence/${$index}`" target="_blank" rel="noopener" type="primary">查看截图</el-link><span v-else>{{ row.detail?.screenshotExpired ? '已过期清理' : '无截图' }}</span></template></el-table-column>
      </el-table>
      <template #footer><el-button :icon="Download" :disabled="!historyTask" @click="exportResults(historyTask || undefined)">导出本任务</el-button><el-button @click="historyDetailOpen=false">关闭</el-button></template>
    </el-dialog>
    <section v-if="view === 'retention'" class="management-section retention-settings">
      <MailStorageSettings />
      <section class="retention-module"><h2>解锁记录保存</h2>
      <el-form label-position="top" :disabled="retentionSaving" class="retention-fields">
        <el-form-item label="截图保留天数"><el-input-number v-model="retentionForm.screenshot_days" :min="1" :max="36500" controls-position="right"/></el-form-item>
        <el-form-item label="任务结果保留天数"><el-input-number v-model="retentionForm.record_days" :min="1" :max="36500" controls-position="right"/></el-form-item>
        <el-form-item label="自动清理过期数据"><el-switch v-model="retentionForm.auto_enabled" aria-label="自动清理过期数据"/></el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" title="运行中、暂停中及待核实记录不会自动清理；防重复凭据始终单独保留。" description="自动清理在打开页面、查看历史或新建任务时检查，每 24 小时最多一次；有任务正在执行时延后。清理不会修改 iLog。"/>
      <div class="management-footer"><el-button type="primary" :loading="retentionSaving" @click="saveRetention">保存规则</el-button></div>
      <div class="retention-cleanup"><div><h2>过期数据清理</h2></div><el-button :disabled="retentionSaving" @click="cleanupExpired">预览并清理过期数据</el-button></div>
      </section>
    </section>
    <el-dialog v-model="reviewOpen" class="bill-review-dialog" title="单号校验与二次确认" width="min(900px, 94vw)" top="4vh">
      <el-descriptions :column="1" border><el-descriptions-item label="业务账号">{{ selectedAccount?.name }}</el-descriptions-item><el-descriptions-item label="费用解锁（小锁）">{{ parsed.fee.length }} 条</el-descriptions-item><el-descriptions-item label="订单解锁（大锁）">{{ parsed.order.length }} 条</el-descriptions-item></el-descriptions>
      <el-alert class="review-alert" type="info" :closable="false" :title="fallbackAccount && fallbackEnabled ? `主账号：${selectedAccount?.name}；兜底账号：${fallbackAccount.name}` : '仅使用主账号'" description="主账号查无订单才使用兜底账号；支持不完整号码查询，将自动处理查询返回的全部业务，并按业务号逐条核实。跨页结果暂不解锁。"/>
      <el-alert v-if="overlap.length" class="review-alert" :title="`${overlap.length} 个提单号同时出现在大小锁列表，将分别执行两种真实操作。`" type="warning" :closable="false"/>
      <el-alert class="review-alert" :type="invalidCount ? 'error' : 'info'" :closable="false" :title="`识别 ${reviewRows.length} 项 · 有效 ${total} 项 · 重复 ${reviewRows.filter(r => r.status === 'duplicate').length} 项 · 异常 ${invalidCount} 项`" description="格式校验不代表订单存在。重复项已排除，异常项需返回修改。"/>
      <el-tabs v-model="reviewKind" style="margin-top:16px">
        <el-tab-pane v-for="kind in kinds" :key="kind.key" :name="kind.key" :label="`${kind.title} · ${parsed[kind.key].length} 条`"/>
      </el-tabs>
      <p style="margin:0 0 12px">本页：有效 {{ parsed[reviewKind].length }} 项 · 重复 {{ duplicateCount(reviewKind) }} 项 · 异常 {{ inspections[reviewKind].filter(r => r.status === 'invalid').length }} 项</p>
      <BillInspectionTable :key="reviewKind" :rows="inspections[reviewKind]" />
      <el-checkbox v-model="reviewAcknowledged" style="margin-top:16px;white-space:normal">我已核对主账号、兜底账号、单号及大小锁类型，并授权本次真实解锁</el-checkbox>
      <el-text v-if="total > 2000" type="danger">每批最多 2000 项，请返回拆分。</el-text>
      <p class="demo-note">将实际修改 iLog 锁状态。单笔异常不阻断后续项，解锁提交不自动重试；{{ verifyAfter ? '标准模式：提交后复查锁状态。' : '快速模式：平台明确成功即记录成功（未复查），不查询最终锁状态。' }}响应不明确则待人工核实。暂停仅停止后续项。</p>
      <template #footer><el-button :disabled="submitting" @click="reviewOpen = false">返回修改</el-button><el-button type="primary" :loading="submitting" :disabled="busy || !runtimeAvailable || !reviewAcknowledged || !!invalidCount || !total || total > 2000" @click="start">确认解锁 {{ total }} 项</el-button></template>
    </el-dialog>
    <el-dialog v-model="accountOpen" :title="editingId ? '编辑业务账号' : '添加业务账号'" width="460px" :close-on-click-modal="!savingAccount" :close-on-press-escape="!savingAccount" :show-close="!savingAccount" @closed="accountPassword = ''">
      <el-form label-position="top" :disabled="savingAccount" @submit.prevent="saveAccount">
        <el-form-item label="账号名称"><el-input v-model="accountForm.name" name="account-name" maxlength="40" placeholder="例如：印尼业务账号"/></el-form-item>
        <el-form-item label="用户名"><el-input v-model="accountForm.username" name="username" maxlength="100" placeholder="输入 iLog 用户名" autocomplete="username"/></el-form-item>
        <el-form-item :label="editingId ? '新密码' : '密码'"><el-input v-model="accountPassword" name="account-password" :aria-label="editingId ? '新密码' : '密码'" type="password" autocomplete="new-password" show-password :placeholder="editingId ? '留空则保留原密码' : '输入账号密码'"/></el-form-item>
      </el-form>
      <p v-if="editingId" class="demo-note">密码留空则保留原密码。</p>
      <template #footer><el-button :disabled="savingAccount" @click="accountOpen = false">取消</el-button><el-button type="primary" :loading="savingAccount" :disabled="!accountForm.name.trim() || !accountForm.username.trim()" @click="saveAccount">保存账号信息</el-button></template>
    </el-dialog>
  </main>
</template>

<script setup lang="ts">
import { platformText } from '../platformLocale'
import StatusBadge from '../components/StatusBadge.vue'
import { computed, nextTick, ref, onBeforeUnmount, onMounted, watch } from 'vue'
import axios from 'axios'
import UnlockAccountsTable from '../components/unlock/UnlockAccountsTable.vue'
import BillInspectionTable from '../components/unlock/BillInspectionTable.vue'
import MailStorageSettings from '../components/MailStorageSettings.vue'
import { Unlock, VideoPlay, VideoPause, Download, RefreshRight, Plus, ArrowLeft, ArrowDown, Setting } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { actionDialog } from '../services/actionDialog'
import { downloadBlob } from '../services/api'
import { inspectBills, duplicateBills } from '../services/billValidation.mjs'
type Kind = 'fee' | 'order'
type Status = 'waiting' | 'processing' | 'success' | 'attention' | 'cancelled'
type Detail = { submitted?: boolean; verification_performed?: boolean; reason?:string; detail?:{timings?:{stage:string;seconds:number}[]}; timings?:{stage:string;seconds:number}[]; total_seconds?: number; status?: string; platform_success?: boolean; before?: {lock?: string;booking?:string}; after?: {lock?: string}; screenshot?: string; screenshotExpired?: boolean }
type Attempt = { account: string; accountName: string; status: string }
type Row = { bill: string; businessNo?:string; kind: Kind; status: Status; message: string; time: string; detail?: Detail; evidenceIndex?: number; actualAccountName?: string; attempts?: Attempt[] }
type Task = { id: string; account: string; accountName: string; fallbackAccount?: string; fallbackAccountName?: string; state: 'running' | 'paused' | 'closing' | 'completed'; rows: Row[]; verify_after?: boolean; createdAt?: string; completedAt?: string; notice?: string; queued?: boolean }
type HistoryItem = Omit<Task,'rows'> & {total:number;attention:number}
const historyOpen=ref(false), historyDetailOpen=ref(false), historyLoading=ref(false)
const historyItems=ref<HistoryItem[]>([]), historyTask=ref<Task|null>(null)
const historyTone = { running: 'primary', paused: 'warning', closing: 'info', completed: 'success' } as const
const historyPage=ref(1), historyTotal=ref(0)
const retentionOpen=ref(false), retentionSaving=ref(false)
const retentionForm=ref({screenshot_days:90,record_days:365,auto_enabled:true})
const savedRetention=ref('')
const formatDate=(value?:string) => value ? new Date(value).toLocaleString('zh-CN') : '—'
const cleanMessage=(value:string) => value.replace(/<br\s*\/?\s*>/gi,' ').replace(/<[^>]*>/g,'')
async function openHistory() { historyOpen.value=true;historyPage.value=1;await loadHistory() }
async function loadHistory() {
  historyLoading.value=true
  try { const response=await axios.get('/api/ilog/tasks',{params:{limit:20,offset:(historyPage.value-1)*20}});historyItems.value=response.data.items;historyTotal.value=response.data.total }
  catch { ElMessage.error('无法读取历史记录') }
  finally { historyLoading.value=false }
}
async function viewHistory(id:string) {
  try { historyTask.value=(await axios.get<Task>(`/api/ilog/tasks/${encodeURIComponent(id)}`)).data;historyDetailOpen.value=true }
  catch { ElMessage.error('记录不存在或已过期清理') }
}
async function openRetention() {
  try { retentionForm.value=(await axios.get('/api/ilog/retention')).data;savedRetention.value=JSON.stringify(retentionForm.value);retentionOpen.value=true }
  catch { ElMessage.error('无法读取保存规则') }
}
async function saveRetention() {
  retentionSaving.value=true
  try { retentionForm.value=(await axios.put('/api/ilog/retention',retentionForm.value,{headers:accountHeaders})).data;savedRetention.value=JSON.stringify(retentionForm.value);ElMessage.success('保存规则已更新') }
  catch { ElMessage.error('保存失败，请检查天数和本地服务') }
  finally { retentionSaving.value=false }
}
async function cleanupExpired() {
  if(JSON.stringify(retentionForm.value)!==savedRetention.value) { ElMessage.warning('请先保存规则，再预览清理');return }
  retentionSaving.value=true
  try {
    const preview=(await axios.get('/api/ilog/retention/preview')).data
    if(preview.busy) { ElMessage.warning('有任务正在执行，暂不清理');return }
    await actionDialog.confirm(`按已保存规则，预计删除 ${preview.tasks} 个历史任务、${preview.screenshots} 张截图、${preview.reports} 份报告。待核实记录和防重复凭据保留。删除不能撤销。`,'确认清理过期数据',{type:'warning',confirmButtonText:'确认清理',cancelButtonText:'取消'})
    const result=(await axios.post('/api/ilog/retention/cleanup',{confirmed:true},{headers:accountHeaders})).data
    if(result.busy) ElMessage.warning('任务已开始，清理已延后')
    else ElMessage.success(`已清理 ${result.tasks} 个任务、${result.screenshots} 张截图、${result.reports} 份报告${result.errors ? `；${result.errors} 个文件未能删除` : ''}`)
    if(historyOpen.value) await loadHistory()
  } catch(error) { if(axios.isAxiosError(error)) ElMessage.error('清理失败，请检查本地服务') }
  finally { retentionSaving.value=false }
}
type Account = { id: string; name: string; username: string; example?: boolean; hasPassword?: boolean }
const props = defineProps<{ view: 'unlock' | 'accounts' | 'history' | 'retention' }>()
watch(() => props.view, (view) => {
  if (view === 'history') void openHistory()
  if (view === 'retention') void openRetention()
}, { immediate: true })
const emit = defineEmits<{ navigate: [value: 'unlock' | 'accounts'] }>()
const storageKey = 'finance-ilog-account-metadata-v1'
const accounts = ref<Account[]>([])
const accountId = ref('')
const accountHeaders = { 'X-Finance-Local': '1' }
const platformAddress = ref('')
const savedPlatformAddress = ref('')
const testAccountId = ref('')
const addressLoaded = ref(false)
const testingAddress = ref(false)
const savingAddress = ref(false)
const addressBusy = computed(() => testingAddress.value || savingAddress.value)

watch(() => props.view, async (view) => {
  if (view !== 'accounts') return
  try {
    const { data } = await axios.get<{ url: string }>('/api/ilog/platform-address')
    savedPlatformAddress.value = platformAddress.value = data.url
    addressLoaded.value = true
  } catch { ElMessage.error(platformText('无法读取平台地址')) }
}, { immediate: true })

watch(accounts, (items) => {
  if (!items.some(item => item.id === testAccountId.value && item.hasPassword)) {
    testAccountId.value = items.find(item => item.hasPassword)?.id || ''
  }
}, { deep: true })

async function savePlatformAddress() {
  savingAddress.value = true
  try {
    const { data } = await axios.put<{ url: string }>('/api/ilog/platform-address', { url: platformAddress.value }, { headers: accountHeaders })
    savedPlatformAddress.value = platformAddress.value = data.url
    ElMessage.success(platformText('平台地址已保存'))
  } catch (error) {
    const detail = axios.isAxiosError(error) ? error.response?.data?.detail : null
    ElMessage.error(platformText(typeof detail === 'string' ? detail : '保存平台地址失败'))
  } finally { savingAddress.value = false }
}

async function testPlatformAddress() {
  testingAddress.value = true
  try {
    await axios.post('/api/ilog/platform-address/test', { url: platformAddress.value, account_id: testAccountId.value }, { headers: accountHeaders })
    ElMessage.success(platformText('登录成功，已打开委托单列表'))
  } catch (error) {
    const detail = axios.isAxiosError(error) ? error.response?.data?.detail : null
    ElMessage.error(platformText(typeof detail === 'string' ? detail : '登录测试失败'))
  } finally { testingAddress.value = false }
}
onMounted(async () => {
  try {
    const response = await axios.get<Account[]>('/api/ilog/accounts')
    accounts.value = response.data
    const legacy: Account[] = JSON.parse(localStorage.getItem(storageKey) || '[]')
    if (Array.isArray(legacy)) {
      for (const account of legacy) {
        if (account.example || !account.name || !account.username || accounts.value.some(a => a.username === account.username)) continue
        const saved = await axios.post<Account>('/api/ilog/accounts', { name: account.name, username: account.username }, { headers: accountHeaders })
        accounts.value.push(saved.data)
      }
    }
    localStorage.removeItem(storageKey)
  } catch { ElMessage.warning('未能读取本机已登记账号，请检查本地服务。') }
  finally { accountId.value = accounts.value[0]?.id || '' }
  try {
    const runtime = await axios.get('/api/ilog/runtime')
    runtimeAvailable.value = runtime.data.available
    runtimeMessage.value = runtime.data.message
    const latest = await axios.get<Task | null>('/api/ilog/tasks/latest')
    if (latest.data && latest.data.state !== 'completed') applyTask(latest.data)
    taskSyncReady.value = true
  } catch (error) { pollError.value = axios.isAxiosError(error) && error.response?.status === 401 ? '登录已失效，请重新登录' : '无法连接任务服务，请检查本地服务状态' }
})
const selectedAccount = computed(() => accounts.value.find(a => a.id === accountId.value))
const fallbackEnabled = ref(true)
const fallbackId = ref('')
const fallbackCandidates = computed(() => accounts.value.filter(a => a.id !== accountId.value && a.hasPassword))
const fallbackAccount = computed(() => fallbackCandidates.value.find(a => a.id === fallbackId.value) || fallbackCandidates.value[0])
const selectedFallbackId = computed({ get: () => fallbackAccount.value?.id || '', set: (value: string) => { fallbackId.value = value } })
const input = ref<Record<Kind, string>>({ fee: '', order: '' })
const kinds: { key: Kind; title: string; placeholder: string }[] = [
  { key: 'fee', title: '费用解锁（小锁）', placeholder: '粘贴需要费用解锁的提单号' },
  { key: 'order', title: '订单解锁（大锁）', placeholder: '粘贴需要订单解锁的提单号' },
]
type VoyageGroup={key:string;account_id:string;account_name:string;bill:string;bills:string[];kind:Kind;ship:string;voyage:string;ship_id:string;voyage_id:string;records:{id:string;booking:string;mbl:string;hbl:string;lock:string;ship:string;voyage:string}[];affected_count:null}
type Preview={token:string;expires_at:string;groups:VoyageGroup[];issues:{bill:string;kind:Kind;message:string}[];can_execute:boolean}
const taskVerifyAfter=ref(true)
const scope=ref<'bill'|'voyage'>('bill'),screenshotPolicy=ref<'errors'|'all'|'none'>('errors'),recordTimings=ref(true),verifyAfter=ref(true)
const preview=ref<Preview|null>(null),previewLoading=ref(false),voyageAcknowledged=ref(false),groupKey=ref(''),previewSignature=ref('')
const signature=computed(()=>JSON.stringify([scope.value,accountId.value,fallbackEnabled.value,fallbackAccount.value?.id,input.value]))
const group=computed(()=>preview.value?.groups.find(g=>g.key===groupKey.value))
const voyageHeading = ref<HTMLElement | null>(null)
const voyageColumns = computed(() => [
  { prop: 'id', label: platformText('记录ID') },
  { prop: 'booking', label: platformText('业务号') },
  { prop: 'mbl', label: platformText('主提单') },
  { prop: 'hbl', label: platformText('分提单') },
  { prop: 'lock', label: platformText('锁状态') },
  { prop: 'ship', label: platformText('船名') },
  { prop: 'voyage', label: platformText('航次') },
])
const now=ref(Date.now()),expiryTimer=setInterval(()=>{now.value=Date.now()},1000)
const expired=computed(()=>!preview.value || !Number.isFinite(Date.parse(preview.value.expires_at)) || Date.parse(preview.value.expires_at)<=now.value)
const canExecuteVoyage=computed(()=>!!preview.value?.token && preview.value.can_execute && !!preview.value.groups.length && !preview.value.issues.length && !expired.value && signature.value===previewSignature.value && voyageAcknowledged.value)
watch(signature,()=>{preview.value=null;previewSignature.value='';groupKey.value='';voyageAcknowledged.value=false;reviewOpen.value=false;reviewAcknowledged.value=false},{flush:'sync'})
async function queryVoyages() {
  if (busy.value || previewLoading.value || invalidCount.value || !total.value || total.value > 2000 || !selectedAccount.value) return
  const requested = signature.value
  preview.value = null
  voyageAcknowledged.value = false
  previewLoading.value = true
  try {
    const { data } = await axios.post<Preview>('/api/ilog/voyages/preview', {
      account_id: accountId.value,
      ...(fallbackEnabled.value && fallbackAccount.value ? { fallback_account_id: fallbackAccount.value.id } : {}),
      fee_bills: parsed.value.fee,
      order_bills: parsed.value.order,
    }, { headers: accountHeaders })
    if (disposed || requested !== signature.value) return
    preview.value = data
    previewSignature.value = requested
    groupKey.value = data.groups[0]?.key || ''
    now.value = Date.now()
    await nextTick()
    voyageHeading.value?.focus()
  } catch {
    if (requested === signature.value) ElMessage.error('航次查询失败，请重新查询后核对')
  } finally {
    previewLoading.value = false
  }
}
const inspections = computed(() => ({ fee: inspectBills(input.value.fee), order: inspectBills(input.value.order) }))
const duplicates = computed(() => ({ fee: duplicateBills(inspections.value.fee), order: duplicateBills(inspections.value.order) }))
const parsed = computed(() => ({ fee: inspections.value.fee.filter(r => r.status === 'valid').map(r => r.bill), order: inspections.value.order.filter(r => r.status === 'valid').map(r => r.bill) }))
const reviewRows = computed(() => kinds.flatMap(k => inspections.value[k.key].map(r => ({ ...r, kind: k.key }))))
const invalidCount = computed(() => reviewRows.value.filter(r => r.status === 'invalid').length)
const duplicateCount = (kind: Kind) => inspections.value[kind].filter(r => r.status === 'duplicate').length
const reviewAcknowledged = ref(false)
const reviewKind = ref<Kind>('fee')
function openReview() {
  reviewAcknowledged.value = false
  reviewKind.value = inspections.value.fee.length ? 'fee' : 'order'
  reviewOpen.value = true
}
const total = computed(() => parsed.value.fee.length + parsed.value.order.length)
const overlap = computed(() => parsed.value.fee.filter(bill => parsed.value.order.includes(bill)))
const rows = ref<Row[]>([])
const taskId = ref('')
const taskState = ref<Task['state'] | ''>('')
const taskQueued = ref(false)
const runtimeAvailable = ref(false)
const runtimeMessage = ref('正在检查真实执行环境')
const submitting = ref(false)
const controlling = ref(false)
const resetting = ref(false)
const taskSyncReady = ref(false)
const pollError = ref('')
const taskNotice = ref('')
const taskStartedAt = ref(0)
let currentTaskId = ''
const busy = computed(() => submitting.value || resetting.value || taskState.value === 'running' || taskState.value === 'paused')
const paused = computed(() => taskState.value === 'paused')
const filter = ref<'all' | 'success' | 'attention' | 'unfinished' | 'cancelled'>('all')
const inputFolded = ref(false)
const progressHeading = ref<HTMLElement | null>(null)
const rowDetailIndex = ref<number | null>(null)
const detailRow = computed(() => rows.value.find(row => row.evidenceIndex === rowDetailIndex.value))
const detailTimings = computed(() => detailRow.value?.detail?.detail?.timings || detailRow.value?.detail?.timings || [])
watch(taskId, id => {
  inputFolded.value = !!id
  rowDetailIndex.value = null
  filter.value = 'all'
})
const reviewOpen = ref(false)
const done = computed(() => rows.value.filter(r => !['waiting','processing'].includes(r.status)).length)
const attentionCount = computed(() => rows.value.filter(r => r.status === 'attention').length)
const resultFilters = computed(() => {
  const cancelled = rows.value.filter(row => row.status === 'cancelled').length
  return [
    { key: 'all', label: platformText('全部结果'), count: rows.value.length },
    { key: 'success', label: platformText('处理成功'), count: done.value - attentionCount.value - cancelled },
    { key: 'attention', label: platformText('待人工处理'), count: attentionCount.value },
    { key: 'unfinished', label: platformText('未完成'), count: rows.value.length - done.value },
    { key: 'cancelled', label: platformText('已取消'), count: cancelled },
  ] as const
})
const percentage = computed(() => rows.value.length ? Math.round(done.value / rows.value.length * 100) : 0)
const currentRow = computed(() => rows.value.find(r => r.status === 'processing'))
const filteredRows = computed(() => {
  if (filter.value === 'all') return rows.value
  if (filter.value === 'unfinished') return rows.value.filter(row => row.status === 'waiting' || row.status === 'processing')
  return rows.value.filter(row => row.status === filter.value)
})
// A platform switch or queue can leave rows waiting for tens of seconds; explain it instead of looking stuck.
const waitingHint = computed(() => taskState.value === 'running' && rows.value.length > 0 && !rows.value.some(r => r.status === 'processing') && now.value - taskStartedAt.value >= 8000)
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let taskVersion = 0
function applyTask(task: Task) {
  ++taskVersion
  taskVerifyAfter.value=task.verify_after !== false
  taskId.value = task.id; taskState.value = task.state; taskAccountName.value = task.accountName
  taskQueued.value = task.state === 'running' && task.queued === true
  taskNotice.value = task.notice || ''
  if (task.id !== currentTaskId) { currentTaskId = task.id; taskStartedAt.value = Date.now() }
  accountId.value = task.account
  fallbackEnabled.value = !!task.fallbackAccount; fallbackId.value = task.fallbackAccount || ''
  taskFallbackName.value = task.fallbackAccountName || ''
  rows.value = task.rows.map((r,index) => ({ ...r, evidenceIndex:index, message: r.message.replace(/<br\s*\/?\s*>/gi,' ').replace(/<[^>]*>/g,''), time: r.time ? new Date(r.time).toLocaleTimeString('zh-CN',{hour12:false}) : '' }))
  clearTimeout(timer)
  if (!disposed && task.state !== 'completed') timer = setTimeout(refreshTask,1500)
}
async function refreshTask() {
  if (!taskId.value || disposed) return
  const requestedId = taskId.value
  const requestedVersion = taskVersion
  try {
    const response = await axios.get<Task>(`/api/ilog/tasks/${encodeURIComponent(requestedId)}`)
    if (taskId.value !== requestedId || requestedVersion !== taskVersion || disposed) return
    pollError.value = ''; applyTask(response.data)
  } catch (error) {
    if (taskId.value !== requestedId || requestedVersion !== taskVersion || disposed) return
    pollError.value = axios.isAxiosError(error) && error.response?.status === 401 ? '登录已失效，请重新登录' : '连接中断，结果尚未同步；请勿重新提交'
    clearTimeout(timer); if (!disposed) timer = setTimeout(refreshTask,4000)
  }
}
async function newTask() {
  if (busy.value || controlling.value || resetting.value || !taskSyncReady.value) return
  resetting.value = true
  try {
    // Soft hint only: the pending receipt never blocks resetting the workspace.
    if (localStorage.getItem('finance-ilog-pending-task')) {
      ElMessage.info('上次提交结果尚未确认；再次提交相同单号会沿用凭据，不会重复解锁')
    }
    if (input.value.fee.trim() || input.value.order.trim()) {
      await actionDialog.confirm('清空当前单号和结果，历史记录保留。', '新建任务', {
        confirmButtonText: '新建任务',
        cancelButtonText: '取消',
      })
    }
    const latest = (await axios.get<Task | null>('/api/ilog/tasks/latest')).data
    if (latest && latest.state === 'closing') {
      // Teardown (closing) only takes seconds: keep the workspace open, a new task queues automatically.
      ElMessage.info('上一个任务正在收尾（约几秒）；现在提交新任务会自动排队衔接，无需等待')
    } else if (latest && latest.state !== 'completed') {
      applyTask(latest)
      ElMessage.warning('有尚未结束的任务，已恢复；请先完成或取消剩余项')
      return
    }
    clearTimeout(timer)
    taskId.value = ''; taskState.value = ''; rows.value = []
    taskQueued.value = false
    taskAccountName.value = ''; taskFallbackName.value = ''
    taskNotice.value = ''; taskStartedAt.value = 0; currentTaskId = ''
    input.value = { fee: '', order: '' }; filter.value = 'all'
    pollError.value = ''; reviewOpen.value = false; reviewAcknowledged.value = false
    ElMessage.success('可以开始新任务，上次结果可在管理 → 解锁历史中查看')
  } catch (error) {
    if (axios.isAxiosError(error)) ElMessage.error('无法确认任务状态，未清空当前工作区')
  } finally { resetting.value = false }
}
async function start() {
  if (busy.value || resetting.value || !taskSyncReady.value || !total.value || !selectedAccount.value || invalidCount.value || (scope.value==='voyage' ? !canExecuteVoyage.value || Date.parse(preview.value!.expires_at)<=Date.now() : !reviewOpen.value || !reviewAcknowledged.value)) return
  if (!runtimeAvailable.value || total.value > 2000) return
  const payload = { account_id: accountId.value, fallback_account_id: fallbackEnabled.value ? fallbackAccount.value?.id || null : null, fee: parsed.value.fee, order: parsed.value.order, confirmed: true, scope:scope.value, ...(scope.value==='voyage'?{preview_token:preview.value!.token}:{}), screenshot_policy:screenshotPolicy.value, record_timings:recordTimings.value, verify_after:verifyAfter.value }
  const signature = JSON.stringify(payload)
  submitting.value = true
  try {
    const saved = JSON.parse(localStorage.getItem('finance-ilog-pending-task') || 'null')
    const request_id = saved?.signature === signature ? saved.id : crypto.randomUUID()
    localStorage.setItem('finance-ilog-pending-task',JSON.stringify({signature,id:request_id}))
    const response = await axios.post<Task>('/api/ilog/tasks',{ ...payload,request_id },{headers:accountHeaders})
    localStorage.removeItem('finance-ilog-pending-task')
    applyTask(response.data); reviewOpen.value = false; filter.value = 'all'
    preview.value = null; voyageAcknowledged.value = false
    await nextTick()
    progressHeading.value?.focus()
    ElMessage.success('任务已提交')
  } catch (error) {
    if (axios.isAxiosError(error)) {
      // A definite 4xx rejection means the task was never created: clear the receipt so the
      // hint cannot get stuck. Unknown outcomes (network error / 5xx) keep the receipt so a
      // retry reuses the same request id and cannot unlock twice.
      if (error.response && error.response.status >= 400 && error.response.status < 500) {
        localStorage.removeItem('finance-ilog-pending-task')
      }
      ElMessage.error(error.response?.data?.detail || '提交结果未确认，请刷新任务状态，不要重复新建')
    } else ElMessage.error('无法保存提交凭据，未建立任务')
  } finally { submitting.value = false }
}
async function controlTask(action: 'pause' | 'resume' | 'cancel') {
  if (!taskId.value || controlling.value) return
  controlling.value = true
  try {
    const response = await axios.post<Task>(`/api/ilog/tasks/${encodeURIComponent(taskId.value)}/control`,{action},{headers:accountHeaders})
    applyTask(response.data)
  } catch { ElMessage.error('任务控制结果未确认，请刷新查看') }
  finally { controlling.value = false }
}
async function cancelRemaining() {
  try { await actionDialog.confirm('只取消尚未开始的单号；当前单会完成并保留结果。','取消剩余项',{type:'warning'}); await controlTask('cancel') } catch { /* User cancelled. */ }
}
const rowClassName = ({ row }: { row: Row }) => row.status === 'processing' ? 'active-unlock-row' : ''
const statusText = (s: Status, detail?: Detail) => detail?.status === 'voyage_platform_confirmed' ? (detail.verification_performed === false ? '航次平台成功（未复查）' : '航次平台确认') : s === 'success' ? ({ platform_accepted:'平台返回成功（未复查）', verified_fee_unlocked:'小锁已解', verified_unlocked:'复查无锁', already_unlocked:'原本已无锁', already_fee_unlocked:'小锁原本已解' }[detail?.status || ''] || '核实通过') : ({ waiting: '等待处理', processing: '真实处理中', attention: '待人工核实', cancelled: '已取消' }[s])
const statusStyle = (s: Status) => ({ waiting: 'info', processing: 'primary', success: 'success', attention: 'warning', cancelled:'info' } as const)[s]
const attemptText = (status: string) => ({ platform_accepted:'平台返回成功（未复查）', not_found:'未找到', ambiguous:'多条匹配', verified_unlocked:'成功并复查无锁', verified_fee_unlocked:'小锁已核实解开', already_unlocked:'原本已无锁', already_fee_unlocked:'小锁原本已解', rejected:'平台拒绝', blocked_duplicate:'已阻止重复提交', platform_confirmed:'平台成功，仍需核实', needs_verification:'待核实' }[status] || '待核实')
function platformResponseText(detail?: Detail) {
  if (detail?.submitted === false || detail?.status === 'blocked_duplicate') return platformText('未提交')
  if (detail?.platform_success === true) return platformText('明确成功')
  if (detail?.platform_success === false) return platformText('明确拒绝')
  return detail ? platformText('未确认') : '—'
}
function lockRecheckText(detail?: Detail) {
  if (detail?.after?.lock) return detail.after.lock
  if (detail?.submitted === false || detail?.status === 'blocked_duplicate' || detail?.verification_performed === false) {
    return platformText('未复查')
  }
  return '—'
}
function exportResults(task?:Task) {
  const cell = (v: string) => `"${(/^[=+@\-\t\r]/.test(v) ? "'" + v : v).replace(/"/g, '""')}"`
  const content = [['主账号', '兜底账号', '实际使用账号', '查询路径', '提单号', '业务号', '类型', '结果', '平台响应', '原锁状态', '复查锁状态', '说明', '时间', '执行模式'], ...(task?.rows || rows.value).map(r => [task?.accountName || taskAccountName.value, task ? task.fallbackAccountName || '' : taskFallbackName.value, r.actualAccountName || '', (r.attempts || []).map(a => `${a.accountName}: ${attemptText(a.status)}`).join(' → '), r.bill, r.businessNo || r.detail?.before?.booking || '', r.kind === 'fee' ? '费用解锁' : '订单解锁', statusText(r.status,r.detail), platformResponseText(r.detail), r.detail?.before?.lock || '', lockRecheckText(r.detail), cleanMessage(r.detail?.status === 'voyage_platform_confirmed' ? r.detail.reason || '航次平台确认，全航次影响数量未提供' : r.message), r.time, (task ? task.verify_after === false : !taskVerifyAfter.value) ? '快速模式（不复查）' : '标准模式（复查）'])].map(r => r.map(cell).join(',')).join('\r\n')
  downloadBlob(new Blob(['\uFEFF', content], { type: 'text/csv;charset=utf-8' }), 'iLog解锁-真实结果.csv')
}
const taskAccountName = ref('')
const taskFallbackName = ref('')
const accountOpen = ref(false)
const editingId = ref('')
const accountPassword = ref('')
const savingAccount = ref(false)
const accountForm = ref({ name: '', username: '' })
function openAccount(account?: Account) { editingId.value = account?.id || ''; accountPassword.value = ''; accountForm.value = { name: account?.name || '', username: account?.username || '' }; accountOpen.value = true }
async function saveAccount() {
  const data = { name: accountForm.value.name.trim(), username: accountForm.value.username.trim() }
  if (!data.name || !data.username || busy.value || savingAccount.value) return
  savingAccount.value = true
  try {
    const payload = { ...data, password: accountPassword.value || null }
    if (editingId.value) {
      const response = await axios.put<Account>(`/api/ilog/accounts/${encodeURIComponent(editingId.value)}`, payload, { headers: accountHeaders })
      accounts.value = accounts.value.map(a => a.id === editingId.value ? response.data : a)
    } else {
      const response = await axios.post<Account>('/api/ilog/accounts', payload, { headers: accountHeaders })
      accounts.value.push(response.data)
      accountId.value = response.data.id
    }
    accountPassword.value = ''
    accountOpen.value = false
    ElMessage.success('账号信息已保存')
  } catch { ElMessage.error('保存失败，请检查本地服务和应用数据目录后重试。') }
  finally { savingAccount.value = false }
}
async function removeAccount(account: Account) {
  try { await actionDialog.confirm(`删除账号“${account.name}”的本机记录？`, '删除账号', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) } catch { return }
  if (busy.value) return
  try {
    await axios.delete(`/api/ilog/accounts/${encodeURIComponent(account.id)}`, { headers: accountHeaders })
    accounts.value = accounts.value.filter(a => a.id !== account.id)
    if (accountId.value === account.id) accountId.value = accounts.value[0]?.id || ''
    ElMessage.success('账号已删除')
  } catch { ElMessage.error('删除失败，请检查本地服务后重试。') }
}
onBeforeUnmount(() => { disposed = true; clearTimeout(timer);clearInterval(expiryTimer) })
</script>

<style scoped>
.accounts-workspace {
  display: grid;
  gap: 16px;
}
.accounts-card-header,
.platform-address-settings > summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
}
.accounts-card-header h2 { margin: 0; font-size: 18px; }
.history-card .history-pagination { justify-content: flex-end; margin-top: 0; }
.management-section.platform-address-settings { padding: 0; }
.platform-address-settings > summary {
  cursor: pointer;
  list-style: none;
}
.platform-address-settings > summary::-webkit-details-marker { display: none; }
.platform-address-settings[open] .disclosure-icon { transform: rotate(180deg); }
.platform-address-form {
  display: grid;
  grid-template-columns: minmax(0, 420px) minmax(0, 240px);
  gap: 12px 16px;
  align-items: end;
  padding: 14px 18px;
  border-top: 1px solid var(--ds-border);
}
.platform-address-form :deep(.el-form-item) { margin-bottom: 0; min-width: 0; }
.platform-address-form :deep(.el-form-item:last-child) { grid-column: 1 / -1; }
@media (max-width: 760px) {
  .platform-address-form { grid-template-columns: minmax(0, 1fr); }
}
.unlock-workspace,
.unlock-draft-fields,
.unlock-draft-sidebar {
  display: grid;
  gap: 16px;
  min-width: 0;
}
.unlock-draft {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  align-items: start;
  gap: 18px;
}
.unlock-card {
  min-width: 0;
  padding: 20px 22px;
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-card);
  background: var(--ds-surface);
}
.unlock-card-head,
.bill-input-footer,
.task-input-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px 16px;
}
.unlock-card-head { margin-bottom: 14px; }
.unlock-card-head h2 {
  margin: 0;
  color: var(--ds-text);
  font-size: 18px;
  font-weight: 650;
}
.config-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.config-row > :last-child { grid-column: 1 / -1; }
.config-field {
  display: grid;
  align-content: start;
  gap: 5px;
  min-width: 0;
}
.config-label {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
  color: var(--ds-text);
  font-size: 13px;
  font-weight: 600;
}
.config-label .el-switch { height: 20px; }
.config-field .el-select { width: 100%; }
.config-field :deep(.el-select__wrapper) { min-height: 36px; }
.config-hint,
.unlock-help,
.progress-caption {
  color: var(--ds-muted);
  font-size: 13px;
  line-height: 1.5;
}
.config-link {
  justify-self: start;
  padding: 0;
  background: none;
  border: 0;
  color: var(--accent);
  font: inherit;
  cursor: pointer;
}
.el-radio-group.unlock-options {
  display: flex;
  align-items: stretch;
  flex-wrap: nowrap;
  justify-self: start;
  max-width: 100%;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--ds-border);
  border-radius: 999px;
  background: var(--ds-bg);
}
.el-radio-group.unlock-options.scope-options { width: 100%; }
.unlock-options :deep(.el-radio-button) {
  display: flex;
  min-width: 0;
}
.scope-options :deep(.el-radio-button) { flex: 1 1 0; }
.el-radio-group.unlock-options :deep(.el-radio-button > .el-radio-button__inner) {
  display: flex;
  width: 100%;
  min-height: 24px;
  align-items: center;
  justify-content: center;
  padding: 2px 9px;
  border: 0;
  border-radius: 999px;
  outline: none;
  background: transparent;
  color: var(--ds-muted);
  box-shadow: none;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.25;
  white-space: normal;
}
.el-radio-group.unlock-options :deep(.el-radio-button.is-active .el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: var(--accent);
  color: white;
  font-weight: 600;
  box-shadow: none;
}
.el-radio-group.unlock-options :deep(.el-radio-button .el-radio-button__original-radio:disabled + .el-radio-button__inner) {
  color: var(--el-disabled-text-color);
}
.el-radio-group.unlock-options :deep(.el-radio-button.is-active .el-radio-button__original-radio:disabled:checked + .el-radio-button__inner) {
  background: var(--ds-surface);
  color: var(--ds-muted);
}
.el-radio-group.unlock-options :deep(.el-radio-button:not(.is-active, .is-disabled):hover > .el-radio-button__inner) {
  color: var(--accent);
}
.el-radio-group.unlock-options :deep(.el-radio-button .el-radio-button__original-radio:focus-visible + .el-radio-button__inner) {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.unlock-help { margin: 0 0 12px; }
.bill-input-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.bill-input-grid .unlock-input-panel {
  min-width: 0;
  padding: 12px;
  background: var(--ds-surface);
  border-color: var(--ds-border);
}
.bill-input-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}
.bill-input-heading label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: var(--ds-text);
  font-size: 14px;
  font-weight: 650;
}
.bill-input-heading .el-icon { color: var(--accent); font-size: 17px; }
.bill-input-heading .count-pill { padding: 3px 8px; font-size: 12px; }
.bill-input-grid textarea {
  min-height: 108px;
  background: #f7f8fa;
  border-color: var(--ds-border);
  line-height: 1.6;
}
.bill-input-footer { margin-top: 12px; }
.bill-input-footer > span { margin: 0; }
.bill-input-footer .el-button { min-height: 40px; }
.unlock-card .demo-note { margin-top: 8px; }

.execution-options { padding: 0; }
.execution-options summary {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 13px 18px;
  list-style: none;
  cursor: pointer;
}
.execution-options summary::-webkit-details-marker { display: none; }
.execution-options summary strong { display: flex; align-items: center; gap: 8px; }
.execution-options summary > span { color: var(--ds-muted); font-size: 12px; text-align: right; }
.execution-options[open] .disclosure-icon { transform: rotate(180deg); }
.execution-options-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 28px;
  padding: 14px 18px;
  border-top: 1px solid var(--ds-border);
}
.execution-timing-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-inline: 18px;
  padding-block: 8px;
  border-top: 1px solid var(--ds-border);
}
.execution-timing-row .el-switch { flex-shrink: 0; }

.task-guide ol {
  display: grid;
  gap: 14px;
  margin: 0;
  padding: 0;
  list-style: none;
  counter-reset: unlock-guide;
}
.task-guide li {
  position: relative;
  display: grid;
  gap: 3px;
  min-height: 34px;
  padding-left: 36px;
  font-size: 13px;
}
.task-guide li::before {
  position: absolute;
  top: 0;
  left: 0;
  display: grid;
  width: 25px;
  height: 25px;
  place-items: center;
  border: 1px solid var(--ds-border);
  border-radius: 50%;
  color: var(--ds-muted);
  background: var(--ds-bg);
  font-weight: 600;
  counter-increment: unlock-guide;
  content: counter(unlock-guide);
}
.task-guide li:first-child::before {
  color: var(--accent);
  border-color: var(--ds-primary-100);
  background: var(--ds-primary-50);
}
.task-guide li > span { color: var(--ds-muted); }

.voyage-review {
  display: grid;
  gap: 12px;
}
.voyage-review .unlock-card-head,
.voyage-review .unlock-help { margin: 0; }
.voyage-review .el-checkbox { height: auto; white-space: normal; }
.voyage-review summary { cursor: pointer; color: var(--accent); font-size: 13px; }
.voyage-review details > .el-table { margin-top: 8px; }
.unlock-properties {
  display: grid;
  grid-template-columns: minmax(105px, .8fr) minmax(0, 2fr);
  margin: 0;
  border: 1px solid var(--ds-border);
  border-radius: 8px;
  font-size: 13px;
}
.unlock-properties > * {
  padding: 9px 11px;
  border-bottom: 1px solid var(--ds-border);
  line-height: 1.5;
}
.unlock-properties > :nth-last-child(-n+2) { border-bottom: 0; }
.unlock-properties dt { color: var(--ds-muted); background: var(--ds-bg); }
.unlock-properties dd { margin: 0; overflow-wrap: anywhere; }

.unlock-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.progress-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.unlock-actions .el-button + .el-button { margin-left: 0; }
.progress-line { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 4px 16px; margin-top: 9px; }
.progress-count { display: flex; align-items: baseline; gap: 6px; margin-bottom: 10px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.progress-count strong { font-size: 33px; line-height: 1.15; color: var(--ds-text); }
.progress-count span { font-size: 16px; color: var(--ds-muted); }
.progress-count small { color: var(--ds-muted); }
.progress-meta { display: flex; flex-wrap: wrap; gap: 4px 12px; color: var(--ds-muted); font-size: 13px; }
.progress-caption { overflow-wrap: anywhere; margin: 0; }
.progress-notice { margin: 6px 0 0; color: var(--ds-muted); font-size: 13px; }
.task-results .el-table { --el-table-header-bg-color: var(--ds-bg); }
.task-results :deep(.active-unlock-row) { --el-table-tr-bg-color: var(--ds-primary-50); }
.task-results :deep(.ds-badge) { white-space: normal; }
.result-filters { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-bottom: 12px; border-bottom: 1px solid var(--ds-border); }
.result-filters button {
  padding: 7px 2px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--ds-muted);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.result-filters button:hover { background: var(--ds-bg); }
.result-filters button[aria-pressed="true"] {
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}
.result-filters button span { margin-left: 4px; font-variant-numeric: tabular-nums; }
.result-footer { display: flex; justify-content: space-between; gap: 12px; margin-top: 12px; color: var(--ds-muted); font-size: 13px; }
.result-footer .el-button { margin-left: auto; }
.bill-no { font-family: ui-monospace, Menlo, Consolas, monospace; overflow-wrap: anywhere; }
.task-input-summary { padding-block: 10px; }
.task-input-summary > div,
.detail-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 14px;
}
.task-input-summary span,
.detail-message { color: var(--ds-muted); font-size: 13px; }
.detail-message { overflow-wrap: anywhere; line-height: 1.6; }

@media (max-width: 1040px) {
  .unlock-draft { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 640px) {
  .unlock-card { padding: 14px; }
  .execution-options { padding: 0; }
  .config-row, .bill-input-grid { grid-template-columns: 1fr; }
  .bill-input-footer .el-button { width: 100%; }
  .execution-options summary { grid-template-columns: minmax(0, 1fr) auto; }
  .execution-options summary > span { grid-column: 1; grid-row: 2; text-align: left; }
  .execution-options summary > .el-icon { grid-row: 1; grid-column: 2; }
}
</style>
