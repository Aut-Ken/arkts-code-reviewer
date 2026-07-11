import img from '@ohos.multimedia.image'
import router from '@ohos.router'

function load(items: Array<PixelMap>, helper: Helper) {
  this.localHelper()
  items.push(img.createPixelMap(buffer))
  helper.on('done', () => {})
  router.pushUrl({ url: 'pages/Detail' })
  setTimeout(() => clearTimeout(timerId), 10)
}
