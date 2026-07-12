import router from '@ohos.router'

function helper(value: string): string {
  return value
}

function run() {
  const marker = '😀'
  const title = $r('app.string.title')
  helper(marker)
  router.back()
}
