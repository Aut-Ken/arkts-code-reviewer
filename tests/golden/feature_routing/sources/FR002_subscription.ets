import emitter from '@ohos.events.emitter'

function subscribe(): void {
  emitter.on({ eventId: 1 }, () => {})
}
