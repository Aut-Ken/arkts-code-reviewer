@Component
struct ParserTail {
  @State first: number = 0
  @State second: number = 0
  aboutToAppear() {
    this.first = 1
  }

  @Styles
  paddingStyle() {
    .padding(1)
  }

  build() {
    Column() {
      ImageAnimator()
        .images([{ src: 'image.png' }])
        .duration(100)
        .state(AnimationStatus.Running)
      Row() {
        Button('run').width(100).padding(5).onClick(() => {
          this.first = 2
        }).margin(5)
        closeDialog()
      }
      Divider()
        .color(Color.Gray)
    }
  }
}
