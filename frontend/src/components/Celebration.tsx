// Les petits moments de fête de « Verre lumineux » : rien que du CSS (pas d'image, pas de canvas, pas de style
// en ligne). Une gerbe de pièces (un gain) ou d'étincelles (une corvée finie) jaillit du toast et un halo doux
// s'allume au fond, une fois, moins d'une seconde. Le mouvement réduit les retire : il reste le toast.
// Le déclencheur est `useCelebrate()` dans ui.tsx ; ce fichier ne fait que dessiner.

export type CelebrationKind = "coin" | "done";

/** Nombre de particules : assez pour une gerbe, trop peu pour des confettis. Les angles sont dans styles.css. */
export const PARTICLES = 10;

export function Celebration({ kind }: { kind: CelebrationKind }) {
  return (
    <div className={`celebrate is-${kind}`} aria-hidden="true">
      <span className="celebrate-glow" />
      <span className="celebrate-burst">
        {Array.from({ length: PARTICLES }, (_, i) => <i key={i} />)}
      </span>
    </div>
  );
}
