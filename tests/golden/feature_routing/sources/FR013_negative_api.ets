function stopTimer(): void {
  clearInterval(1)
}

function unrelatedListener(): void {
  SDK.on('event', () => {})
}
