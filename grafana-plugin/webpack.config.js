const path = require('path');

module.exports = (env = {}) => {
  const isProduction = env.production;
  return {
    mode: isProduction ? 'production' : 'development',
    entry: './src/module.ts',
    output: {
      path: path.resolve(__dirname, 'dist'),
      filename: 'module.js',
      library: { type: 'amd' },
    },
    resolve: {
      extensions: ['.ts', '.tsx', '.js', '.jsx'],
    },
    module: {
      rules: [
        {
          test: /\.tsx?$/,
          use: {
            loader: 'swc-loader',
            options: {
              jsc: {
                target: 'es2020',
                parser: { syntax: 'typescript', tsx: true },
              },
            },
          },
          exclude: /node_modules/,
        },
        {
          test: /\.css$/,
          use: ['style-loader', 'css-loader'],
        },
        {
          test: /\.(png|svg|jpg|jpeg|gif)$/,
          type: 'asset/resource',
        },
      ],
    },
    externals: [
      function ({ request }, callback) {
        const grafanaLibs = ['@grafana/data', '@grafana/ui', '@grafana/runtime', 'react', 'react-dom'];
        if (grafanaLibs.includes(request)) {
          return callback(null, 'amd ' + request);
        }
        callback();
      },
    ],
  };
};
