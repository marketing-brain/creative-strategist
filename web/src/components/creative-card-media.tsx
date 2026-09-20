'use client';
import { useEffect, useRef, useState } from 'react';

// A card's picture. A video shows its poster, a small still cut from the file, and plays
// muted only while the pointer rests on it, so a page of fifty cards opens no mp4 until the
// reader asks for one. A video with no poster yet waits until it is near the viewport before
// it lets the browser fetch a first frame.
// `eager` is for the cards on the first screen; the rest load as they are scrolled to.
export function CardMedia({ poster, video, alt, eager = false }: { poster: string | null; video: string | null; alt: string; eager?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [playing, setPlaying] = useState(false);
  const [near, setNear] = useState(false);

  useEffect(() => {
    if (poster || !video || !box.current) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNear(true);
          io.disconnect();
        }
      },
      { rootMargin: '300px' },
    );
    io.observe(box.current);
    return () => io.disconnect();
  }, [poster, video]);

  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  // A short rest before playing, so sweeping the pointer across the grid starts nothing.
  const enter = (e: React.PointerEvent) => {
    if (!video || e.pointerType !== 'mouse') return;
    timer.current = setTimeout(() => setPlaying(true), 180);
  };
  const leave = () => {
    if (timer.current) clearTimeout(timer.current);
    setPlaying(false);
  };

  const fill = 'absolute inset-0 h-full w-full object-cover';
  return (
    <div ref={box} onPointerEnter={enter} onPointerLeave={leave} className="absolute inset-0">
      {poster ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={poster} alt={alt} loading={eager ? 'eager' : 'lazy'} fetchPriority={eager ? 'high' : undefined} decoding="async" className={`${fill} transition-transform duration-300 group-hover:scale-[1.03]`} />
      ) : video && near ? (
        <video src={`${video}#t=0.5`} muted playsInline preload="metadata" aria-label={alt} className={fill} />
      ) : null}
      {playing && video && (
        <video
          // React does not reliably write `muted` to the element, and an unmuted video may
          // not autoplay, so the preview is muted and started by hand.
          ref={(el) => {
            if (!el) return;
            el.muted = true;
            el.play().catch(() => {});
          }}
          src={video}
          muted
          loop
          playsInline
          aria-hidden="true"
          className={fill}
        />
      )}
    </div>
  );
}
