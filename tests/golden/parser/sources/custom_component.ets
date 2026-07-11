import { window } from '@kit.ArkUI'

@ComponentV2
struct ImageGeneratorDialog {
  private curWindow?: window.Window = undefined

  @Builder
  pageMap(name: string) {
    if (name === 'home') {
      CanvasHome({})
    } else {
      TextTouchUpComponent()
    }
  }

  initWinSizeChangeCallback() {
    this.curWindow?.on('windowSizeChange', () => {})
  }

  build() {
    Navigation() {
    }
      .hideNavBar(true)
  }
}
