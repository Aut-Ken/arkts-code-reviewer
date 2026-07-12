@Component
struct LifecycleImage {
  build() {
    Image('hero.png')
      .onAppear(() => {})
      .onError(() => {})
  }
}
