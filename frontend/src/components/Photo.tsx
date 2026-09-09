import { useEffect, useState } from 'react';

export function Photo({ blob, alt }: { blob: Blob; alt: string }) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    const next = URL.createObjectURL(blob);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [blob]);
  return url ? <img className="photo" src={url} alt={alt} /> : null;
}
