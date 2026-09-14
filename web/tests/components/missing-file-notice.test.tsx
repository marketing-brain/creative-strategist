import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MissingFileNotice } from '@/components/missing-file-notice';
import { formatStatus } from '@/lib/creative-format';

describe('MissingFileNotice', () => {
  it('explains why a proven video has no file and what still works', () => {
    render(<MissingFileNotice status={formatStatus({ format: 'unresolved', evidence_format: 'video', primary_asset_url: null })} />);
    expect(screen.getByRole('heading', { name: 'Video · no file' })).toBeInTheDocument();
    expect(screen.getByText(/made only for the ad/)).toBeInTheDocument();
    expect(screen.getByText(/Spend and delivery metrics still count/)).toBeInTheDocument();
  });
});
