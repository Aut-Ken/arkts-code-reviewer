function changedTimer(): void {
  setInterval(() => {}, 1000);
}
function requiredCleanup(): void {
  clearInterval(1);
}
function helpfulLogger(): void {
  console.info('timer');
}
