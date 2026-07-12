@Component
struct Decorated {
  @State count: number = 0

  @Builder
  content() {
    Text('value')
  }

  build() {
    this.content()
  }
}
