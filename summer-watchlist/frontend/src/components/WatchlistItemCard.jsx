import React from 'react';
import { motion } from 'framer-motion';
import { MoreVertical, Check, Play, Edit3, Trash2, Calendar as CalIcon } from 'lucide-react';


const WatchlistItemCard = ({ item, onEdit, onDelete, onUpdate }) => {
  const [showMenu, setShowMenu] = React.useState(false);

  const getPriorityColor = (priority) => {
    switch(priority) {
      case 'High': return 'text-red-500 bg-red-500/10';
      case 'Medium': return 'text-yellow-500 bg-yellow-500/10';
      case 'Low': return 'text-green-500 bg-green-500/10';
      default: return 'text-gray-500 bg-gray-500/10';
    }
  };

  const handleProgress = (e) => {
    e.stopPropagation();
    if (item.progress < item.total_episodes) {
      let status = item.status;
      if (item.status === 'To Watch') status = 'Watching';
      const newProgress = item.progress + 1;
      if (newProgress === item.total_episodes) status = 'Completed';
      
      onUpdate(item._id, newProgress, status);
    }
  };

  const handleComplete = (e) => {
    e.stopPropagation();
    onUpdate(item._id, item.total_episodes, 'Completed');
  };

  return (
    <div className="glass-card p-4 relative group hover:border-primary/50 transition-colors">
      <div className="flex justify-between items-start mb-2">
        <div className="flex gap-2">
          <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-1 rounded-md ${getPriorityColor(item.priority)}`}>
            {item.priority}
          </span>
          <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-1 rounded-md bg-primary/10 text-primary">
            {item.type}
          </span>
        </div>
        
        <div className="relative">
          <button 
            onClick={() => setShowMenu(!showMenu)} 
            className="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 text-textMuted"
            onBlur={() => setTimeout(() => setShowMenu(false), 200)}
          >
            <MoreVertical className="w-4 h-4" />
          </button>
          
          {showMenu && (
            <div className="absolute right-0 top-full mt-1 w-32 bg-surface shadow-xl border border-gray-100 dark:border-gray-700 rounded-lg py-1 z-10">
              <button onClick={onEdit} className="w-full text-left px-4 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2">
                <Edit3 className="w-4 h-4" /> Edit
              </button>
              <button onClick={onDelete} className="w-full text-left px-4 py-2 text-sm text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 flex items-center gap-2">
                <Trash2 className="w-4 h-4" /> Delete
              </button>
            </div>
          )}
        </div>
      </div>

      <h4 className="font-bold text-base leading-tight mb-1 line-clamp-2">{item.title}</h4>
      
      {item.genre && <p className="text-xs text-textMuted mb-3 truncate">{item.genre}</p>}

      {item.deadline && (
        <div className="flex items-center gap-1 text-xs text-secondary mb-3">
          <CalIcon className="w-3 h-3" />
          {new Date(item.deadline).toLocaleDateString()}
        </div>
      )}

      <div className="mt-4 pt-3 border-t border-gray-100 dark:border-gray-700">
        <div className="flex justify-between text-xs mb-1">
          <span className="text-textMuted">Progress</span>
          <span className="font-medium">{item.progress} / {item.total_episodes}</span>
        </div>
        
        <div className="h-1.5 w-full bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden mb-3">
          <motion.div 
            className="h-full bg-gradient-to-r from-primary to-secondary"
            initial={{ width: 0 }}
            animate={{ width: `${(item.progress / item.total_episodes) * 100}%` }}
            transition={{ duration: 0.5 }}
          />
        </div>

        <div className="flex gap-2">
          {item.status !== 'Completed' && (
            <button 
              onClick={handleProgress}
              className="flex-1 flex justify-center items-center gap-1 text-xs py-1.5 bg-primary/10 text-primary hover:bg-primary hover:text-white rounded-md transition-colors"
            >
              <Play className="w-3 h-3" /> +1 Ep
            </button>
          )}
          {item.status !== 'Completed' && (
            <button 
              onClick={handleComplete}
              className="flex-1 flex justify-center items-center gap-1 text-xs py-1.5 bg-green-500/10 text-green-600 dark:text-green-400 hover:bg-green-500 hover:text-white rounded-md transition-colors"
            >
              <Check className="w-3 h-3" /> Finish
            </button>
          )}
          {item.status === 'Completed' && (
            <div className="w-full text-center text-xs py-1.5 text-green-500 font-medium">
              Completed! 🎉
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default WatchlistItemCard;
