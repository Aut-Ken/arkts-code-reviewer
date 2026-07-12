@Component
struct StatefulPage {
  @State count: number = 0

  aboutToAppear(): void {
    this.count++
  }
}
