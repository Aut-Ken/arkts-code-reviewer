# Worker 通信

主线程向 Worker 发送的对象必须满足可传输要求。

```ts
worker.postMessage(payload);
```
