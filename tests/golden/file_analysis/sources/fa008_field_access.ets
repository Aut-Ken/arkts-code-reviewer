@Component
struct Counter {
  count: number = 0

  update() {
    this.count += 1
    this.count = this.count + 1
  }

  build() {
    Text(`${this.count}`)
  }
}
