import { AvatarManager } from "../compose/AvatarManager";
import { useStore } from "../../store/useStore";

export function AvatarsView() {
  return <div className="scroll-area">
    <div className="avatar-library-heading">
      <h1>Avatars</h1>
      <p className="muted">Your saved people, characters and subjects.</p>
    </div>
    <AvatarManager embedded onClose={() => useStore.getState().setView("compose")} />
  </div>;
}
