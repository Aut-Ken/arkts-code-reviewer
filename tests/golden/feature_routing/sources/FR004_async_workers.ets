import taskpool from '@ohos.taskpool'
import worker from '@ohos.worker'

async function dispatchWork(): Promise<void> {
  await taskpool.execute(() => 1)
  worker.ThreadWorker
}
