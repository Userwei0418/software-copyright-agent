import { useEffect, useState } from "react";
import { listRecentTasks, RecentTask, SidecarConnection } from "./api";

export function ProjectSwitcher({ connection, taskId, onChange }: {
  connection: SidecarConnection | null; taskId: string; onChange: (taskId: string) => void;
}) {
  const [items, setItems] = useState<RecentTask[]>([]);
  useEffect(() => {
    if (!connection) return;
    listRecentTasks(connection).then(setItems).catch(() => setItems([]));
  }, [connection, taskId]);
  return <label className="project-switcher"><small>当前项目</small>
    <select value={taskId} onChange={(event) => onChange(event.target.value)}>
      <option value="">请选择项目</option>
      {items.map((item) => <option key={item.task_id} value={item.task_id}>
        {item.display_name} · {item.task_id.slice(0, 6)}
      </option>)}
    </select></label>;
}
