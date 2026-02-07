import React, { useState, useEffect, useCallback } from 'react';
import { MapPin, Plus, Trash2, Route, Clock, Navigation, Loader2, Settings, RotateCcw, Copy, ChevronDown, ChevronUp } from 'lucide-react';

// TSP Solver using Held-Karp algorithm (optimal for up to ~15-20 stops)
function solveTSPHeldKarp(distanceMatrix) {
  const n = distanceMatrix.length;
  if (n <= 1) return { order: [0], distance: 0 };
  if (n === 2) return { order: [0, 1], distance: distanceMatrix[0][1] };
  
  // For very small problems, use brute force
  if (n <= 8) {
    return solveTSPBruteForce(distanceMatrix);
  }
  
  // Held-Karp dynamic programming
  const INF = Infinity;
  const ALL_VISITED = (1 << n) - 1;
  
  // dp[mask][i] = minimum distance to visit all nodes in mask, ending at i
  const dp = Array(1 << n).fill(null).map(() => Array(n).fill(INF));
  const parent = Array(1 << n).fill(null).map(() => Array(n).fill(-1));
  
  // Start from node 0
  dp[1][0] = 0;
  
  for (let mask = 1; mask <= ALL_VISITED; mask++) {
    for (let last = 0; last < n; last++) {
      if (!(mask & (1 << last))) continue;
      if (dp[mask][last] === INF) continue;
      
      for (let next = 0; next < n; next++) {
        if (mask & (1 << next)) continue;
        
        const newMask = mask | (1 << next);
        const newDist = dp[mask][last] + distanceMatrix[last][next];
        
        if (newDist < dp[newMask][next]) {
          dp[newMask][next] = newDist;
          parent[newMask][next] = last;
        }
      }
    }
  }
  
  // Find best ending node (for open TSP - no return to start)
  let minDist = INF;
  let lastNode = -1;
  for (let i = 1; i < n; i++) {
    if (dp[ALL_VISITED][i] < minDist) {
      minDist = dp[ALL_VISITED][i];
      lastNode = i;
    }
  }
  
  // Reconstruct path
  const order = [];
  let mask = ALL_VISITED;
  let curr = lastNode;
  
  while (curr !== -1) {
    order.push(curr);
    const prev = parent[mask][curr];
    mask ^= (1 << curr);
    curr = prev;
  }
  
  order.reverse();
  return { order, distance: minDist };
}

// Brute force for small problems
function solveTSPBruteForce(distanceMatrix) {
  const n = distanceMatrix.length;
  const nodes = Array.from({ length: n - 1 }, (_, i) => i + 1);
  
  let bestOrder = null;
  let bestDistance = Infinity;
  
  // Generate all permutations
  function permute(arr, l = 0) {
    if (l === arr.length - 1) {
      const order = [0, ...arr];
      let dist = 0;
      for (let i = 0; i < order.length - 1; i++) {
        dist += distanceMatrix[order[i]][order[i + 1]];
      }
      if (dist < bestDistance) {
        bestDistance = dist;
        bestOrder = [...order];
      }
      return;
    }
    
    for (let i = l; i < arr.length; i++) {
      [arr[l], arr[i]] = [arr[i], arr[l]];
      permute(arr, l + 1);
      [arr[l], arr[i]] = [arr[i], arr[l]];
    }
  }
  
  permute(nodes);
  return { order: bestOrder, distance: bestDistance };
}

// Nearest neighbor heuristic (for comparison)
function solveTSPNearestNeighbor(distanceMatrix) {
  const n = distanceMatrix.length;
  if (n <= 1) return { order: [0], distance: 0 };
  
  const visited = new Set([0]);
  const order = [0];
  let totalDistance = 0;
  let current = 0;
  
  while (visited.size < n) {
    let nearest = -1;
    let nearestDist = Infinity;
    
    for (let i = 0; i < n; i++) {
      if (!visited.has(i) && distanceMatrix[current][i] < nearestDist) {
        nearest = i;
        nearestDist = distanceMatrix[current][i];
      }
    }
    
    visited.add(nearest);
    order.push(nearest);
    totalDistance += nearestDist;
    current = nearest;
  }
  
  return { order, distance: totalDistance };
}

// Google-style optimization (simulated - reorders to minimize backtracking)
function solveTSPGoogleStyle(distanceMatrix) {
  // This simulates Google's greedy approach
  return solveTSPNearestNeighbor(distanceMatrix);
}

export default function RouteOptimizer() {
  const [apiKey, setApiKey] = useState('');
  const [apiKeySet, setApiKeySet] = useState(false);
  const [stops, setStops] = useState([
    { id: 1, address: '', lat: null, lng: null, validated: false }
  ]);
  const [depot, setDepot] = useState({ address: '', lat: null, lng: null, validated: false });
  const [useDepot, setUseDepot] = useState(true);
  const [optimizationMode, setOptimizationMode] = useState('distance'); // 'distance' or 'time'
  const [isLoading, setIsLoading] = useState(false);
  const [isValidating, setIsValidating] = useState(false);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  
  const addStop = () => {
    const newId = Math.max(0, ...stops.map(s => s.id)) + 1;
    setStops([...stops, { id: newId, address: '', lat: null, lng: null, validated: false }]);
  };
  
  const removeStop = (id) => {
    if (stops.length > 1) {
      setStops(stops.filter(s => s.id !== id));
    }
  };
  
  const updateStop = (id, address) => {
    setStops(stops.map(s => 
      s.id === id ? { ...s, address, validated: false, lat: null, lng: null } : s
    ));
  };
  
  const updateDepot = (address) => {
    setDepot({ address, lat: null, lng: null, validated: false });
  };
  
  // Geocode an address using Google Geocoding API
  const geocodeAddress = async (address) => {
    const response = await fetch(
      `https://maps.googleapis.com/maps/api/geocode/json?address=${encodeURIComponent(address)}&key=${apiKey}`
    );
    const data = await response.json();
    
    if (data.status === 'OK' && data.results.length > 0) {
      const location = data.results[0].geometry.location;
      return {
        lat: location.lat,
        lng: location.lng,
        formatted: data.results[0].formatted_address
      };
    }
    throw new Error(`Could not geocode: ${address}`);
  };
  
  // Validate all addresses
  const validateAddresses = async () => {
    setIsValidating(true);
    setError(null);
    
    try {
      const allStops = useDepot && depot.address ? [depot, ...stops] : stops;
      const validStops = allStops.filter(s => s.address.trim());
      
      if (validStops.length < 2) {
        throw new Error('Please enter at least 2 stops');
      }
      
      // Geocode depot if used
      if (useDepot && depot.address && !depot.validated) {
        const result = await geocodeAddress(depot.address);
        setDepot({ ...depot, ...result, validated: true, address: result.formatted });
      }
      
      // Geocode all stops
      const updatedStops = await Promise.all(
        stops.map(async (stop) => {
          if (!stop.address.trim()) return stop;
          if (stop.validated) return stop;
          
          try {
            const result = await geocodeAddress(stop.address);
            return { ...stop, ...result, validated: true, address: result.formatted };
          } catch (e) {
            return { ...stop, error: e.message };
          }
        })
      );
      
      setStops(updatedStops);
      
      const hasErrors = updatedStops.some(s => s.error);
      if (hasErrors) {
        throw new Error('Some addresses could not be validated');
      }
      
    } catch (e) {
      setError(e.message);
    } finally {
      setIsValidating(false);
    }
  };
  
  // Get distance matrix from Google API
  const getDistanceMatrix = async (locations) => {
    const origins = locations.map(l => `${l.lat},${l.lng}`).join('|');
    const destinations = origins;
    
    const response = await fetch(
      `https://maps.googleapis.com/maps/api/distancematrix/json?origins=${origins}&destinations=${destinations}&key=${apiKey}`
    );
    const data = await response.json();
    
    if (data.status !== 'OK') {
      throw new Error(`Distance Matrix API error: ${data.status}`);
    }
    
    // Build matrices
    const n = locations.length;
    const distanceMatrix = [];
    const timeMatrix = [];
    
    for (let i = 0; i < n; i++) {
      distanceMatrix[i] = [];
      timeMatrix[i] = [];
      
      for (let j = 0; j < n; j++) {
        const element = data.rows[i].elements[j];
        if (element.status === 'OK') {
          distanceMatrix[i][j] = element.distance.value; // meters
          timeMatrix[i][j] = element.duration.value; // seconds
        } else {
          distanceMatrix[i][j] = Infinity;
          timeMatrix[i][j] = Infinity;
        }
      }
    }
    
    return { distanceMatrix, timeMatrix, raw: data };
  };
  
  // Main optimization function
  const optimizeRoute = async () => {
    setIsLoading(true);
    setError(null);
    setResults(null);
    
    try {
      // Get all valid locations
      const validStops = stops.filter(s => s.validated && s.lat && s.lng);
      let allLocations = validStops;
      
      if (useDepot && depot.validated) {
        allLocations = [depot, ...validStops];
      }
      
      if (allLocations.length < 2) {
        throw new Error('Need at least 2 validated locations');
      }
      
      if (allLocations.length > 20) {
        throw new Error('Maximum 20 stops supported for optimal solving');
      }
      
      // Get distance matrix
      const { distanceMatrix, timeMatrix } = await getDistanceMatrix(allLocations);
      
      // Choose matrix based on optimization mode
      const matrix = optimizationMode === 'distance' ? distanceMatrix : timeMatrix;
      
      // Solve using different methods
      const optimalResult = solveTSPHeldKarp(matrix);
      const greedyResult = solveTSPNearestNeighbor(matrix);
      
      // Calculate metrics for both
      const calculateMetrics = (order) => {
        let totalDistance = 0;
        let totalTime = 0;
        for (let i = 0; i < order.length - 1; i++) {
          totalDistance += distanceMatrix[order[i]][order[i + 1]];
          totalTime += timeMatrix[order[i]][order[i + 1]];
        }
        return { totalDistance, totalTime };
      };
      
      const optimalMetrics = calculateMetrics(optimalResult.order);
      const greedyMetrics = calculateMetrics(greedyResult.order);
      
      // Build ordered stops list
      const orderedStops = optimalResult.order.map(i => ({
        ...allLocations[i],
        originalIndex: i
      }));
      
      const greedyOrderedStops = greedyResult.order.map(i => ({
        ...allLocations[i],
        originalIndex: i
      }));
      
      // Calculate savings
      const distanceSaved = greedyMetrics.totalDistance - optimalMetrics.totalDistance;
      const timeSaved = greedyMetrics.totalTime - optimalMetrics.totalTime;
      
      setResults({
        optimal: {
          order: optimalResult.order,
          stops: orderedStops,
          ...optimalMetrics
        },
        greedy: {
          order: greedyResult.order,
          stops: greedyOrderedStops,
          ...greedyMetrics
        },
        savings: {
          distance: distanceSaved,
          time: timeSaved,
          distancePercent: (distanceSaved / greedyMetrics.totalDistance) * 100,
          timePercent: (timeSaved / greedyMetrics.totalTime) * 100
        },
        matrix: { distance: distanceMatrix, time: timeMatrix },
        mode: optimizationMode
      });
      
    } catch (e) {
      setError(e.message);
    } finally {
      setIsLoading(false);
    }
  };
  
  const formatDistance = (meters) => {
    const miles = meters / 1609.34;
    return `${miles.toFixed(1)} mi`;
  };
  
  const formatTime = (seconds) => {
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    if (hours > 0) {
      return `${hours}h ${mins}m`;
    }
    return `${mins} min`;
  };
  
  const copyRouteToClipboard = () => {
    if (!results) return;
    const text = results.optimal.stops
      .map((s, i) => `${i + 1}. ${s.address || s.formatted}`)
      .join('\n');
    navigator.clipboard.writeText(text);
  };
  
  const generateGoogleMapsUrl = () => {
    if (!results) return '';
    const stops = results.optimal.stops;
    const origin = `${stops[0].lat},${stops[0].lng}`;
    const destination = `${stops[stops.length - 1].lat},${stops[stops.length - 1].lng}`;
    const waypoints = stops.slice(1, -1).map(s => `${s.lat},${s.lng}`).join('|');
    
    let url = `https://www.google.com/maps/dir/?api=1&origin=${origin}&destination=${destination}`;
    if (waypoints) {
      url += `&waypoints=${waypoints}`;
    }
    return url;
  };
  
  const resetAll = () => {
    setStops([{ id: 1, address: '', lat: null, lng: null, validated: false }]);
    setDepot({ address: '', lat: null, lng: null, validated: false });
    setResults(null);
    setError(null);
  };

  // API Key entry screen
  if (!apiKeySet) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 p-6 flex items-center justify-center">
        <div className="bg-slate-800 rounded-2xl shadow-2xl p-8 max-w-md w-full border border-slate-700">
          <div className="text-center mb-6">
            <div className="w-16 h-16 bg-blue-500/20 rounded-full flex items-center justify-center mx-auto mb-4">
              <Navigation className="w-8 h-8 text-blue-400" />
            </div>
            <h1 className="text-2xl font-bold text-white mb-2">Route Optimizer</h1>
            <p className="text-slate-400 text-sm">BEV Fleet Routing Baseline Tool</p>
          </div>
          
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-2">
                Google Maps API Key
              </label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Enter your API key"
                className="w-full px-4 py-3 bg-slate-700 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
              <p className="mt-2 text-xs text-slate-500">
                Requires Geocoding, Distance Matrix, and Directions APIs enabled
              </p>
            </div>
            
            <button
              onClick={() => apiKey && setApiKeySet(true)}
              disabled={!apiKey}
              className="w-full py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-600 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors"
            >
              Continue
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 p-4 md:p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-500/20 rounded-lg flex items-center justify-center">
              <Navigation className="w-5 h-5 text-blue-400" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Route Optimizer</h1>
              <p className="text-xs text-slate-400">Isuzu BEV Fleet Baseline</p>
            </div>
          </div>
          <button
            onClick={resetAll}
            className="flex items-center gap-2 px-3 py-2 text-slate-400 hover:text-white transition-colors"
          >
            <RotateCcw className="w-4 h-4" />
            <span className="text-sm">Reset</span>
          </button>
        </div>

        <div className="grid md:grid-cols-2 gap-6">
          {/* Input Panel */}
          <div className="space-y-4">
            {/* Depot/Start Location */}
            <div className="bg-slate-800 rounded-xl p-4 border border-slate-700">
              <div className="flex items-center justify-between mb-3">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={useDepot}
                    onChange={(e) => setUseDepot(e.target.checked)}
                    className="rounded border-slate-600 bg-slate-700 text-blue-500 focus:ring-blue-500"
                  />
                  <span className="text-sm font-medium text-slate-300">Start from depot</span>
                </label>
              </div>
              
              {useDepot && (
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 bg-green-500/20 rounded-lg flex items-center justify-center flex-shrink-0">
                    <MapPin className="w-4 h-4 text-green-400" />
                  </div>
                  <input
                    type="text"
                    value={depot.address}
                    onChange={(e) => updateDepot(e.target.value)}
                    placeholder="Depot address"
                    className="flex-1 px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg text-white text-sm placeholder-slate-400 focus:ring-2 focus:ring-blue-500"
                  />
                  {depot.validated && (
                    <span className="text-green-400 text-xs">✓</span>
                  )}
                </div>
              )}
            </div>

            {/* Stops */}
            <div className="bg-slate-800 rounded-xl p-4 border border-slate-700">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-medium text-slate-300">Delivery Stops</h2>
                <span className="text-xs text-slate-500">{stops.filter(s => s.address).length} stops</span>
              </div>
              
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {stops.map((stop, index) => (
                  <div key={stop.id} className="flex items-center gap-2">
                    <div className="w-8 h-8 bg-blue-500/20 rounded-lg flex items-center justify-center flex-shrink-0">
                      <span className="text-xs font-medium text-blue-400">{index + 1}</span>
                    </div>
                    <input
                      type="text"
                      value={stop.address}
                      onChange={(e) => updateStop(stop.id, e.target.value)}
                      placeholder={`Stop ${index + 1} address`}
                      className={`flex-1 px-3 py-2 bg-slate-700 border rounded-lg text-white text-sm placeholder-slate-400 focus:ring-2 focus:ring-blue-500 ${
                        stop.error ? 'border-red-500' : 'border-slate-600'
                      }`}
                    />
                    {stop.validated && (
                      <span className="text-green-400 text-xs">✓</span>
                    )}
                    <button
                      onClick={() => removeStop(stop.id)}
                      className="p-2 text-slate-400 hover:text-red-400 transition-colors"
                      disabled={stops.length === 1}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>
              
              <button
                onClick={addStop}
                className="mt-3 flex items-center gap-2 text-sm text-blue-400 hover:text-blue-300 transition-colors"
              >
                <Plus className="w-4 h-4" />
                Add stop
              </button>
            </div>

            {/* Options */}
            <div className="bg-slate-800 rounded-xl p-4 border border-slate-700">
              <button
                onClick={() => setShowAdvanced(!showAdvanced)}
                className="flex items-center justify-between w-full text-sm font-medium text-slate-300"
              >
                <span>Optimization Settings</span>
                {showAdvanced ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
              </button>
              
              {showAdvanced && (
                <div className="mt-3 space-y-3">
                  <div>
                    <label className="block text-xs text-slate-400 mb-2">Optimize for</label>
                    <div className="flex gap-2">
                      <button
                        onClick={() => setOptimizationMode('distance')}
                        className={`flex-1 py-2 px-3 text-sm rounded-lg transition-colors ${
                          optimizationMode === 'distance'
                            ? 'bg-blue-600 text-white'
                            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                        }`}
                      >
                        Distance
                      </button>
                      <button
                        onClick={() => setOptimizationMode('time')}
                        className={`flex-1 py-2 px-3 text-sm rounded-lg transition-colors ${
                          optimizationMode === 'time'
                            ? 'bg-blue-600 text-white'
                            : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                        }`}
                      >
                        Time
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Error Display */}
            {error && (
              <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4">
                <p className="text-red-400 text-sm">{error}</p>
              </div>
            )}

            {/* Action Buttons */}
            <div className="flex gap-3">
              <button
                onClick={validateAddresses}
                disabled={isValidating || isLoading}
                className="flex-1 py-3 bg-slate-700 hover:bg-slate-600 disabled:bg-slate-700 disabled:cursor-not-allowed text-white font-medium rounded-xl transition-colors flex items-center justify-center gap-2"
              >
                {isValidating ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <MapPin className="w-4 h-4" />
                )}
                Validate
              </button>
              <button
                onClick={optimizeRoute}
                disabled={isLoading || isValidating || stops.filter(s => s.validated).length < 1}
                className="flex-1 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-600 disabled:cursor-not-allowed text-white font-medium rounded-xl transition-colors flex items-center justify-center gap-2"
              >
                {isLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Route className="w-4 h-4" />
                )}
                Optimize
              </button>
            </div>
          </div>

          {/* Results Panel */}
          <div className="space-y-4">
            {results ? (
              <>
                {/* Summary Cards */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="bg-slate-800 rounded-xl p-4 border border-slate-700">
                    <div className="text-xs text-slate-400 mb-1">Total Distance</div>
                    <div className="text-2xl font-bold text-white">
                      {formatDistance(results.optimal.totalDistance)}
                    </div>
                    {results.savings.distance > 0 && (
                      <div className="text-xs text-green-400 mt-1">
                        {formatDistance(results.savings.distance)} saved vs greedy
                      </div>
                    )}
                  </div>
                  <div className="bg-slate-800 rounded-xl p-4 border border-slate-700">
                    <div className="text-xs text-slate-400 mb-1">Total Time</div>
                    <div className="text-2xl font-bold text-white">
                      {formatTime(results.optimal.totalTime)}
                    </div>
                    {results.savings.time > 0 && (
                      <div className="text-xs text-green-400 mt-1">
                        {formatTime(results.savings.time)} saved vs greedy
                      </div>
                    )}
                  </div>
                </div>

                {/* Optimized Route */}
                <div className="bg-slate-800 rounded-xl p-4 border border-slate-700">
                  <div className="flex items-center justify-between mb-3">
                    <h2 className="text-sm font-medium text-slate-300">Optimized Route Order</h2>
                    <button
                      onClick={copyRouteToClipboard}
                      className="p-2 text-slate-400 hover:text-white transition-colors"
                      title="Copy to clipboard"
                    >
                      <Copy className="w-4 h-4" />
                    </button>
                  </div>
                  
                  <div className="space-y-2">
                    {results.optimal.stops.map((stop, index) => (
                      <div key={index} className="flex items-start gap-3">
                        <div className={`w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 text-xs font-medium ${
                          index === 0 
                            ? 'bg-green-500/20 text-green-400' 
                            : index === results.optimal.stops.length - 1
                            ? 'bg-red-500/20 text-red-400'
                            : 'bg-blue-500/20 text-blue-400'
                        }`}>
                          {index + 1}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm text-white truncate">
                            {stop.address || stop.formatted}
                          </p>
                          {index < results.optimal.stops.length - 1 && (
                            <p className="text-xs text-slate-500 mt-1">
                              → {formatDistance(results.matrix.distance[results.optimal.order[index]][results.optimal.order[index + 1]])}
                              {' · '}
                              {formatTime(results.matrix.time[results.optimal.order[index]][results.optimal.order[index + 1]])}
                            </p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Comparison */}
                <div className="bg-slate-800 rounded-xl p-4 border border-slate-700">
                  <h2 className="text-sm font-medium text-slate-300 mb-3">Algorithm Comparison</h2>
                  <div className="space-y-3">
                    <div className="flex items-center justify-between p-3 bg-blue-500/10 rounded-lg border border-blue-500/20">
                      <div>
                        <div className="text-sm font-medium text-white">Optimal (Held-Karp)</div>
                        <div className="text-xs text-slate-400">Guaranteed best solution</div>
                      </div>
                      <div className="text-right">
                        <div className="text-sm text-white">{formatDistance(results.optimal.totalDistance)}</div>
                        <div className="text-xs text-slate-400">{formatTime(results.optimal.totalTime)}</div>
                      </div>
                    </div>
                    <div className="flex items-center justify-between p-3 bg-slate-700/50 rounded-lg">
                      <div>
                        <div className="text-sm font-medium text-slate-300">Greedy (Nearest Neighbor)</div>
                        <div className="text-xs text-slate-500">Google Maps style heuristic</div>
                      </div>
                      <div className="text-right">
                        <div className="text-sm text-slate-300">{formatDistance(results.greedy.totalDistance)}</div>
                        <div className="text-xs text-slate-500">{formatTime(results.greedy.totalTime)}</div>
                      </div>
                    </div>
                    {results.savings.distancePercent > 0 && (
                      <div className="text-center text-xs text-green-400 pt-2">
                        Optimal route is {results.savings.distancePercent.toFixed(1)}% shorter than greedy approach
                      </div>
                    )}
                  </div>
                </div>

                {/* Open in Google Maps */}
                <a
                  href={generateGoogleMapsUrl()}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block w-full py-3 bg-slate-700 hover:bg-slate-600 text-white font-medium rounded-xl transition-colors text-center"
                >
                  Open in Google Maps →
                </a>
              </>
            ) : (
              <div className="bg-slate-800 rounded-xl p-8 border border-slate-700 text-center">
                <div className="w-16 h-16 bg-slate-700 rounded-full flex items-center justify-center mx-auto mb-4">
                  <Route className="w-8 h-8 text-slate-500" />
                </div>
                <h3 className="text-lg font-medium text-slate-300 mb-2">No route optimized yet</h3>
                <p className="text-sm text-slate-500">
                  Add your stops, validate addresses, then click Optimize to find the best route.
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="mt-8 text-center text-xs text-slate-500">
          <p>Baseline route optimizer for Isuzu BEV Fleet project • Uses Held-Karp algorithm for guaranteed optimal TSP solution</p>
          <p className="mt-1">Supports up to 20 stops • Compare against advanced routing approaches</p>
        </div>
      </div>
    </div>
  );
}
