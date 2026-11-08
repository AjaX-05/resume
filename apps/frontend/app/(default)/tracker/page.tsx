'use client';

import React, { useState } from 'react';
import { PageFrame } from '@/components/ui/page-frame';
import { KanbanBoard } from '@/components/tracker/kanban-board';
import { ScraperPanel } from '@/components/tracker/scraper-panel';

export default function TrackerPage() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [postedFilterMinutes, setPostedFilterMinutes] = useState<number | null>(null);
  const [kickTaskId, setKickTaskId] = useState<string | null>(null);

  return (
    <PageFrame width="wide" height="screen" className="h-full flex flex-col">
      <ScraperPanel
        onComplete={() => setRefreshKey((k) => k + 1)}
        postedFilterMinutes={postedFilterMinutes}
        onPostedFilterChange={setPostedFilterMinutes}
        onApplyTask={setKickTaskId}
      />
      {/* flex flex-col so KanbanBoard's own flex-1 can fill the remaining height */}
      <div className="flex flex-col flex-1 min-h-0">
        <KanbanBoard
          reloadToken={refreshKey}
          postedFilterMinutes={postedFilterMinutes}
          kickTaskId={kickTaskId}
        />
      </div>
    </PageFrame>
  );
}
