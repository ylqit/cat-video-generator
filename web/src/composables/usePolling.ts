import { onBeforeUnmount } from "vue";

/**
 * 周期执行回调的自调度轮询；interval() 每次重新求值以支持活跃时加速。
 */
export function usePolling(
  fn: () => Promise<void> | void,
  interval: () => number,
) {
  let timer: number | undefined;
  let stopped = false;

  async function tick() {
    if (stopped) {
      return;
    }
    try {
      await fn();
    } catch {
      // 单次轮询失败不打断后续节奏，错误由调用方状态展示
    } finally {
      if (!stopped) {
        timer = window.setTimeout(tick, interval());
      }
    }
  }

  function start() {
    if (timer === undefined) {
      void tick();
    }
  }

  function stop() {
    stopped = true;
    if (timer !== undefined) {
      window.clearTimeout(timer);
      timer = undefined;
    }
  }

  onBeforeUnmount(stop);
  return { start, stop };
}
