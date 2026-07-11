import img from '@ohos.multimedia.image'
import router from '@ohos.router'

@Entry
@Component
struct PhotoWall {
  @State photos: PixelMap[] = []
  private timerId: number = 0

  aboutToDisappear() {
    clearInterval(this.timerId)
  }

  async loadImages() {
    const pixelMap = await img.createPixelMap(buffer)
    this.photos.push(pixelMap)
    this.timerId = setInterval(() => {
      router.pushUrl({ url: 'pages/Detail' })
    }, 1000)
  }

  build() {
    Column() {
      Grid() {
        ForEach(this.photos, (photo: PixelMap) => {
          Image(photo)
            .objectFit(ImageFit.Cover)
            .onClick(() => this.loadImages())
        })
      }
    }
  }
}
