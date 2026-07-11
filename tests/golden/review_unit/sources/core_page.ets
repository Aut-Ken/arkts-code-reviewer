import router from '@ohos.router'

@Entry
@Component
struct CorePage {
  @State count: number = 0

  first() {
    this.count += 1
  }

  second() {
    router.back()
  }

  build() {
    Column() {
      Text(`${this.count}`)
    }
  }
}
