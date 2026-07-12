import abilityAccessCtrl from '@ohos.abilityAccessCtrl'
import http from '@ohos.net.http'
import preferences from '@ohos.data.preferences'

function submit(value: string): void {
  abilityAccessCtrl.requestPermissionsFromUser([])
  http.createHttp()
  preferences.getPreferences()
  TextInput({ text: value })
}
