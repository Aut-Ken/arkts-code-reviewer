async function work(): Promise<void> {
  await Promise.resolve()
  try {
    [1].map((value) => value)
  } catch (error) {
  }
}
