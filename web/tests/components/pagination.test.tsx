import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Pagination } from '@/components/pagination';

const makeHref = (page: number) => `/creatives?page=${page}`;

describe('Pagination', () => {
  it('renders nothing when there is only one page', () => {
    const { container } = render(<Pagination page={1} totalPages={1} makeHref={makeHref} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('links Prev/Next and page numbers, marking the current page', () => {
    render(<Pagination page={3} totalPages={5} makeHref={makeHref} />);
    expect(screen.getByLabelText('Page 3')).toHaveAttribute('aria-current', 'page');
    expect(screen.getByLabelText('Page 2')).toHaveAttribute('href', '/creatives?page=2');
    expect(screen.getByLabelText('Previous page')).toHaveAttribute('href', '/creatives?page=2');
    expect(screen.getByLabelText('Next page')).toHaveAttribute('href', '/creatives?page=4');
  });

  it('disables Previous on page 1 and Next on the last page', () => {
    render(<Pagination page={1} totalPages={3} makeHref={makeHref} />);
    expect(screen.getByLabelText('Previous page')).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByLabelText('Previous page')).not.toHaveAttribute('href');
    expect(screen.getByLabelText('Next page')).toHaveAttribute('aria-disabled', 'false');
  });

  it('collapses a large page range with an ellipsis', () => {
    render(<Pagination page={10} totalPages={20} makeHref={makeHref} />);
    expect(screen.getByLabelText('Page 1')).toBeInTheDocument();
    expect(screen.getByLabelText('Page 20')).toBeInTheDocument();
    expect(screen.getAllByText('…').length).toBeGreaterThan(0);
    expect(screen.queryByLabelText('Page 5')).not.toBeInTheDocument();
  });
});
