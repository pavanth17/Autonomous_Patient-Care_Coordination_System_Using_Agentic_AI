import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { X } from 'lucide-react';

const AddEditModal = ({ item, onClose, onSave }) => {
  const [formData, setFormData] = useState({
    title: '',
    type: 'movie',
    genre: '',
    priority: 'Medium',
    status: 'To Watch',
    progress: 0,
    total_episodes: 1,
    deadline: '',
    notes: ''
  });

  useEffect(() => {
    if (item) {
      setFormData({
        ...item,
        deadline: item.deadline ? new Date(item.deadline).toISOString().split('T')[0] : ''
      });
    }
  }, [item]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    onSave(formData);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
      <motion.div 
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.9 }}
        className="bg-surface w-full max-w-lg rounded-2xl shadow-2xl overflow-hidden border border-gray-200 dark:border-gray-700"
      >
        <div className="flex justify-between items-center p-6 border-b border-gray-100 dark:border-gray-800">
          <h2 className="text-xl font-bold">{item ? 'Edit Item' : 'Add to Watchlist'}</h2>
          <button onClick={onClose} className="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4 max-h-[70vh] overflow-y-auto">
          <div>
            <label className="block text-sm font-medium mb-1">Title *</label>
            <input 
              type="text" name="title" required
              value={formData.title} onChange={handleChange}
              className="input-field" placeholder="e.g. Stranger Things"
            />
          </div>
          
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Type</label>
              <select name="type" value={formData.type} onChange={handleChange} className="input-field">
                <option value="movie">Movie</option>
                <option value="series">Series</option>
                <option value="anime">Anime</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Priority</label>
              <select name="priority" value={formData.priority} onChange={handleChange} className="input-field">
                <option value="Low">Low</option>
                <option value="Medium">Medium</option>
                <option value="High">High</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Genre</label>
              <input 
                type="text" name="genre" 
                value={formData.genre} onChange={handleChange}
                className="input-field" placeholder="e.g. Sci-Fi"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Status</label>
              <select name="status" value={formData.status} onChange={handleChange} className="input-field">
                <option value="To Watch">To Watch</option>
                <option value="Watching">Watching</option>
                <option value="Completed">Completed</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Total Episodes</label>
              <input 
                type="number" name="total_episodes" min="1"
                value={formData.total_episodes} onChange={handleChange}
                className="input-field"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Deadline</label>
              <input 
                type="date" name="deadline" 
                value={formData.deadline} onChange={handleChange}
                className="input-field"
              />
            </div>
          </div>

          <div className="pt-4 flex gap-3">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">Cancel</button>
            <button type="submit" className="btn-primary flex-1">{item ? 'Save Changes' : 'Add Item'}</button>
          </div>
        </form>
      </motion.div>
    </div>
  );
};

export default AddEditModal;
