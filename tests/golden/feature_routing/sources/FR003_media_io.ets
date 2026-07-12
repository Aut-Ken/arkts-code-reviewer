import media from '@ohos.multimedia.media'
import fs from '@ohos.file.fs'

function persistMedia(): void {
  media.createAVPlayer()
  fs.openSync('clip.dat')
}
