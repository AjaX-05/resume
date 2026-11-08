'use client';

import React, { useState } from 'react';
import { PageFrame } from '@/components/ui/page-frame';
import { KanbanBoard } from '@/components/tracker/kanban-board';
import { ScraperPanel } from '@/components/tracker/scraper-panel';

export default function TrackerPage() {
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <PageFrame width="wide" height="screen" className="h-full flex flex-col">
      <ScraperPanel onComplete={() => setRefreshKey((k) => k + 1)} />
      {/* flex flex-col so KanbanBoard's own flex-1 can fill the remaining height */}
      <div className="flex flex-col flex-1 min-h-0">
        <KanbanBoard key={refreshKey} />
      </div>
    </PageFrame>
  );
}
