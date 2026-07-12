import router from '@ohos.router'

function safe() {
  // router.pushUrl({ url: 'comment' })
  const sample = "router.pushUrl({ url: 'string' })"
  router.back()
}

function broken() {
  router.pushUrl(
}

@Component
struct MissingEnd {
  build() { Text('x') }
