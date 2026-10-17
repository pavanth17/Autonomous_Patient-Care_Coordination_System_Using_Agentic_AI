import React, { useState, useEffect, useContext } from 'react';
import { AuthContext } from '../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import axios from 'axios';

const Login = () => {
  const [profiles, setProfiles] = useState([]);
  const [error, setError] = useState('');
  const { login } = useContext(AuthContext);
  const navigate = useNavigate();

  useEffect(() => {
    const fetchProfiles = async () => {
      try {
        const res = await axios.get('http://localhost:5000/api/auth/profiles');
        setProfiles(res.data);
      } catch (err) {
        setError('Failed to load profiles. Ensure backend is running.');
      }
    };
    fetchProfiles();
  }, []);

  const handleSelectProfile = async (username) => {
    try {
      await login(username);
      navigate('/');
    } catch (err) {
      setError('Failed to login as ' + username);
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-background px-4">
      <motion.div 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="text-center mb-12"
      >
        <h1 className="text-5xl font-bold mb-4 neon-text-primary tracking-wider">WatchVibe</h1>
        <p className="text-xl text-textMuted">Who's watching?</p>
      </motion.div>

      {error && <div className="bg-neonRed/20 text-neonRed border border-neonRed p-3 rounded-lg mb-6 text-center shadow-neon-red">{error}</div>}

      <div className="flex flex-wrap justify-center gap-8">
        {profiles.map((profile, i) => (
          <motion.div
            key={profile._id}
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: i * 0.1 }}
            className="flex flex-col items-center group cursor-pointer"
            onClick={() => handleSelectProfile(profile.username)}
          >
            <div 
              className="w-32 h-32 rounded-2xl overflow-hidden border-4 transition-all duration-300 group-hover:scale-110 shadow-glass"
              style={{ borderColor: 'transparent' }}
              onMouseEnter={(e) => e.currentTarget.style.borderColor = profile.color || '#00f2fe'}
              onMouseLeave={(e) => e.currentTarget.style.borderColor = 'transparent'}
            >
              <img src={profile.avatarUrl || `https://api.dicebear.com/7.x/avataaars/svg?seed=${profile.username}`} alt={profile.username} className="w-full h-full object-cover bg-white/10" />
            </div>
            <p className="mt-4 text-xl font-medium text-textMuted group-hover:text-white transition-colors">
              {profile.username}
            </p>
          </motion.div>
        ))}
        {profiles.length === 0 && !error && <p className="text-white">Loading profiles...</p>}
      </div>
    </div>
  );
};

export default Login;
