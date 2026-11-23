export function Placeholder({ title, description }: { title: string; description: string }) {
  return (
    <div>
      <h2 className="text-lg mb-1">{title}</h2>
      <p className="text-sm text-text-muted">{description}</p>
      <p className="text-sm mt-2">This screen is built in its own milestone reply. The shell and navigation already work.</p>
    </div>
  );
}
