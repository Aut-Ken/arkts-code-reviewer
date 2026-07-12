import router from '@ohos.router'

function navigate() {
  router.back()
}

function destructure({ router }: { router: object }) {
  router.pushUrl({ url: 'shadow' })
}

function catchShadow() {
  try {
    throw new Error('failed')
  } catch (router) {
    router.pushUrl({ url: 'shadow' })
  }
}

function arrowShadow() {
  const invoke = (router: object) => router.pushUrl({ url: 'shadow' })
  invoke({})
}
