@Component
struct Title {
  @State title: ResourceStr = $r('app.string.title')

  build() {
    Text(this.title)
  }
}
