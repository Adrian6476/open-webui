import { afterEach, describe, expect, it, vi } from 'vitest';
import { createInitialChatRead } from './chatSnapshot';

afterEach(() => {
	vi.useRealTimers();
});

describe('initial chat snapshot', () => {
	it('reads after room acceptance and leaves later joins to refresh', async () => {
		vi.useFakeTimers();
		const read = createInitialChatRead('chat', new AbortController().signal);
		const fetchSnapshot = vi.fn();
		const loading = read.waitForJoin().then(fetchSnapshot);
		await vi.advanceTimersByTimeAsync(100);
		expect(fetchSnapshot).not.toHaveBeenCalled();
		expect(read.acceptJoin()).toBe(true);
		await loading;
		expect(fetchSnapshot).toHaveBeenCalledOnce();
		expect(read.acceptJoin()).toBe(false);
		expect(vi.getTimerCount()).toBe(0);
	});

	it('remembers acceptance received before waiting begins', async () => {
		const read = createInitialChatRead('chat', new AbortController().signal);
		expect(read.acceptJoin()).toBe(true);
		await read.waitForJoin();
		expect(read.acceptJoin()).toBe(false);
	});

	it('falls back after 1.5 seconds and requires a late join to refresh', async () => {
		vi.useFakeTimers();
		const read = createInitialChatRead('chat', new AbortController().signal);
		const fetchSnapshot = vi.fn();
		const loading = read.waitForJoin().then(fetchSnapshot);
		await vi.advanceTimersByTimeAsync(1499);
		expect(fetchSnapshot).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(1);
		await loading;
		expect(fetchSnapshot).toHaveBeenCalledOnce();
		expect(read.acceptJoin()).toBe(false);
	});

	it('releases an abandoned navigation without delaying the next chat', async () => {
		vi.useFakeTimers();
		const controller = new AbortController();
		const oldRead = createInitialChatRead('old', controller.signal);
		const oldLoading = oldRead.waitForJoin();
		controller.abort();
		await oldLoading;
		expect(oldRead.acceptJoin()).toBe(false);
		expect(vi.getTimerCount()).toBe(0);
		const nextRead = createInitialChatRead('next', new AbortController().signal);
		expect(nextRead.acceptJoin()).toBe(true);
		await nextRead.waitForJoin();
	});

	it('does not wait or allocate a timer for an already aborted navigation', async () => {
		vi.useFakeTimers();
		const controller = new AbortController();
		controller.abort();
		const read = createInitialChatRead('chat', controller.signal);
		await read.waitForJoin();
		expect(read.acceptJoin()).toBe(false);
		expect(vi.getTimerCount()).toBe(0);
	});
});
