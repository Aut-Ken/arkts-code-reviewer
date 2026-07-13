/**
 * @syscap SystemCapability.Utils.Lang
 * @since 9
 */
declare namespace taskpool {
  class Task {
    constructor(func: Function);
    static isCanceled(): boolean;
  }
}
