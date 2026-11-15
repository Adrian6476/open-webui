/** Coordinate the first snapshot with room membership; reconnects still refresh. */
export function createInitialChatRead(id: string, signal: AbortSignal) {
	let started = false;
	let resolveJoin!: () => void;
	const joined = new Promise<void>((resolve) => {
		resolveJoin = resolve;
	});

	return {
		id,
		signal,
		acceptJoin() {
			if (signal.aborted || started) return false;
			resolveJoin();
			return true;
		},
		async waitForJoin() {
			if (signal.aborted) {
				started = true;
				return;
			}
			let timer: ReturnType<typeof setTimeout> | undefined;
			try {
				signal.addEventListener('abort', resolveJoin, { once: true });
				await Promise.race([
					joined,
					new Promise<void>((resolve) => {
						timer = setTimeout(resolve, 1500);
					})
				]);
			} finally {
				clearTimeout(timer);
				signal.removeEventListener('abort', resolveJoin);
				started = true;
			}
		}
	};
}
