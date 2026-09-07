import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { describe, expect, it, vi } from 'vitest';
import ErrorBoundary from '@/app/(app)/error';
import GlobalErrorBoundary from '@/app/global-error';

const err = Object.assign(new Error('backend unreachable'), { digest: 'abc123' });

describe('app error boundaries', () => {
  it('shows a message and retries via reset()', async () => {
    const reset = vi.fn();
    render(<ErrorBoundary error={err} reset={reset} />);
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
    expect(screen.getByText(/abc123/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /try again/i }));
    expect(reset).toHaveBeenCalled();
  });

  it('global-error renders its own <html>/<body> shell', () => {
    // It replaces the root layout, so it must supply the document itself.
    // jsdom cannot nest an <html> inside a container, so assert on the element
    // tree rather than rendering it.
    const tree = GlobalErrorBoundary({ error: err, reset: () => {} }) as ReactElement<{
      children: ReactElement;
    }>;
    expect(tree.type).toBe('html');
    expect(tree.props.children.type).toBe('body');
  });
});
