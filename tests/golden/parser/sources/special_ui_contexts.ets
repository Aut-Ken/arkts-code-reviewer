@CustomDialog
struct DialogContent {
  build() {
    Text('dialog')
  }
}

@Component
struct SpecialContexts {
  controller: CustomDialogController = new CustomDialogController({
    builder: DialogContent()
  })

  runBusiness() {
    Factory().start()
    TestRunner.bind(this)(task)
  }

  pageTransition() {
    PageTransitionEnter({ duration: 100 }).onEnter(() => {})
    PageTransitionExit({ duration: 100 }).onExit(() => {})
  }

  build() {
    Column() {
      String('not a component').trim()
      Text('content')
    }
  }
}
