import defaultImage, {
  PixelMap as LocalPixelMap,
  Size
} from '@ohos.multimedia.image'
import * as routerApi from '@ohos.router'
import '@ohos.hilog'

function create(): LocalPixelMap {
  routerApi.back()
  return defaultImage.createPixelMap(buffer)
}
