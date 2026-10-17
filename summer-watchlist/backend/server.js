const express = require('express');
const cors = require('cors');
const mongoose = require('mongoose');
const dotenv = require('dotenv');
const { MongoMemoryServer } = require('mongodb-memory-server');

dotenv.config();

const app = express();

app.use(cors());
app.use(express.json());

app.use('/api/auth', require('./routes/auth'));
app.use('/api/watchlist', require('./routes/watchlist'));

const PORT = process.env.PORT || 5000;

// Set up MongoDB Memory Server
let mongoServer;

const User = require('./models/User');

const seedUsers = async () => {
  const users = [
    { username: 'Friend1', color: '#ff0055', avatarUrl: 'https://api.dicebear.com/7.x/avataaars/svg?seed=Friend1' },
    { username: 'Friend2', color: '#00ffcc', avatarUrl: 'https://api.dicebear.com/7.x/avataaars/svg?seed=Friend2' },
    { username: 'Friend3', color: '#ffcc00', avatarUrl: 'https://api.dicebear.com/7.x/avataaars/svg?seed=Friend3' },
  ];
  for (const u of users) {
    const exists = await User.findOne({ username: u.username });
    if (!exists) {
      await User.create(u);
    }
  }
  console.log('Seed users created.');
};

const connectDB = async () => {
  try {
    mongoServer = await MongoMemoryServer.create();
    const uri = mongoServer.getUri();
    
    await mongoose.connect(uri);
    console.log(`MongoDB In-Memory connected at ${uri}`);
    await seedUsers();
    
    app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
  } catch (err) {
    console.error('Failed to start MongoDB Memory Server', err);
    process.exit(1);
  }
};

connectDB();

// Handle graceful shutdown
process.on('SIGINT', async () => {
  if (mongoServer) {
    await mongoose.disconnect();
    await mongoServer.stop();
  }
  process.exit(0);
});
