struct OptionalLifecycle {
  aboutToAppear?(): void {
    setTimeout(() => {}, 1)
  }
}
