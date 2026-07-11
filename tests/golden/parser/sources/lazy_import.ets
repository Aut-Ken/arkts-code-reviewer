import lazy { Foo as Bar } from 'mod'

function useLazy(value: Bar) {
  value.run()
}
