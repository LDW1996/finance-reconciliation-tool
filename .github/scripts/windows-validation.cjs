// A fast build may reuse only a successful Windows check for this exact source.
module.exports = async ({github, context, core}) => {
  try {
    const {data} = await github.rest.actions.listWorkflowRuns({
      ...context.repo, workflow_id: 'windows-tests.yml',
      head_sha: context.sha, status: 'completed', per_page: 100,
    });
    const passed = data.workflow_runs.some(run =>
      run.head_sha === context.sha && run.conclusion === 'success' &&
      ['push', 'workflow_dispatch'].includes(run.event));
    if (!passed) {
      core.setFailed('当前提交尚无通过的 Windows Tests 记录。请取消“快速打包”运行完整流程，或等待同一提交的 Windows Tests 通过后再试。');
      return;
    }
    await core.summary.addRaw('快速打包：当前提交已通过独立 Windows 全量测试，本次复用结果；仍执行 EXE 生成与启动检查。').write();
  } catch (error) {
    core.setFailed('无法核验 Windows 测试记录，未跳过验证。请取消“快速打包”运行完整流程，或稍后重试。');
  }
};
