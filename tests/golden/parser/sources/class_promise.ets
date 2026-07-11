class TaskRunner {
  async run(values: number[]): Promise<number> {
    const result = await Promise.resolve(values[0])
    return result
  }
}
