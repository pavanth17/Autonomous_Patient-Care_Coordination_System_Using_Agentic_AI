import React from 'react';
import { motion } from 'framer-motion';

const CalendarView = ({ items }) => {
  // Simple timeline view of items with deadlines
  const itemsWithDeadline = items
    .filter(i => i.deadline)
    .sort((a, b) => new Date(a.deadline) - new Date(b.deadline));

  if (itemsWithDeadline.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-textMuted bg-surface rounded-2xl border border-dashed border-gray-300 dark:border-gray-700">
        <p>No items with deadlines yet.</p>
        <p className="text-sm mt-2">Add a deadline to your watchlist items to see them here.</p>
      </div>
    );
  }

  return (
    <div className="bg-surface rounded-2xl p-6 shadow-sm border border-gray-100 dark:border-gray-800">
      <h3 className="text-xl font-bold mb-6">Upcoming Deadlines</h3>
      <div className="relative border-l-2 border-primary/30 ml-4 space-y-8">
        {itemsWithDeadline.map((item, index) => {
          const date = new Date(item.deadline);
          const isPast = date < new Date();
          
          return (
            <motion.div 
              key={item._id}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: index * 0.1 }}
              className="relative pl-6"
            >
              <div className={`absolute -left-[9px] top-1.5 w-4 h-4 rounded-full border-2 border-surface ${isPast ? 'bg-red-500' : 'bg-primary'}`}></div>
              
              <div className="glass-card p-4 inline-block min-w-[250px]">
                <div className="text-sm font-medium text-primary mb-1">
                  {date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}
                  {isPast && <span className="ml-2 text-red-500 text-xs">(Past)</span>}
                </div>
                <h4 className="font-bold text-lg">{item.title}</h4>
                <div className="flex gap-2 mt-2">
                  <span className="text-xs bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded">{item.type}</span>
                  <span className="text-xs bg-gray-100 dark:bg-gray-700 px-2 py-1 rounded">{item.status}</span>
                </div>
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
};

export default CalendarView;
