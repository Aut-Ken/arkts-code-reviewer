@Component
struct Styled {
  build() {
    Text('hello')
      .fontSize(20)
      .onClick(() => this.tap())
  }

  tap() {
  }
}
