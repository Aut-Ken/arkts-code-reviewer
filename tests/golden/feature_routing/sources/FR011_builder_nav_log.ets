import router from '@ohos.router'
import hilog from '@ohos.hilog'

@Builder
function destination(): void {
  Navigation()
  router.pushUrl({ url: 'pages/Next' })
  hilog.info(0, 'route')
}
