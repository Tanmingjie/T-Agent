interface Props {
  files: Record<string, string>;
  onSelect: (filename: string) => void;
  selected: string | null;
}

export default function FileTree({ files, onSelect, selected }: Props) {
  const filenames = Object.keys(files);
  return (
    <div>
      <h3 className="font-semibold mb-2 text-sm">文件</h3>
      {filenames.map((fn) => (
        <button
          key={fn}
          onClick={() => onSelect(fn)}
          className={`block w-full text-left px-3 py-1.5 text-sm rounded mb-0.5 font-mono ${
            selected === fn ? "bg-brand-50" : "hover:bg-gray-50"
          }`}
        >
          📄 {fn}
        </button>
      ))}
    </div>
  );
}
