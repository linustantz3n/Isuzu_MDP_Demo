import React, { useState } from 'react';
import { MapPin, Plus, Trash2, Route, Navigation, Loader2, AlertCircle, CheckCircle, Download, Home } from 'lucide-react';

const API_BASE = 'http://localhost:5001/api';

// Demo addresses from test_stops_wide.txt
const DEMO_ADDRESSES = [
  'Detroit, MI',
  'Ann Arbor, MI',
  'Flint, MI',
  'Lansing, MI',
  'Grand Rapids, MI',
  'Kalamazoo, MI',
  'Battle Creek, MI',
  'Jackson, MI',
  'Dearborn, MI',
  'Livonia, MI'
];

function RouteOptimizer() {
  const [addresses, setAddresses] = useState(['', '', '']);
  const [depot, setDepot] = useState('');
  const [useDepot, setUseDepot] = useState(false);
  const [validatedLocations, setValidatedLocations] = useState([]);
  const [validatedDepot, setValidatedDepot] = useState(null);
  const [loading, setLoading] = useState(false);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);

  const loadDemoAddresses = () => {
    setAddresses(DEMO_ADDRESSES);
    setDepot('');
    setUseDepot(false);
    setValidatedLocations([]);
    setValidatedDepot(null);
    setResults(null);
    setError(null);
  };

  const addAddress = () => {
    setAddresses([...addresses, '']);
    setValidatedLocations([]);
    setResults(null);
  };

  const removeAddress = (index) => {
    if (addresses.length > 2) {
      setAddresses(addresses.filter((_, i) => i !== index));
      setValidatedLocations([]);
      setResults(null);
    }
  };

  const updateAddress = (index, value) => {
    const newAddresses = [...addresses];
    newAddresses[index] = value;
    setAddresses(newAddresses);
    setValidatedLocations([]);
    setResults(null);
  };

  const updateDepot = (value) => {
    setDepot(value);
    setValidatedDepot(null);
    setResults(null);
  };

  const validateAddresses = async () => {
    setValidating(true);
    setError(null);
    setResults(null);

    const validAddresses = addresses.filter(a => a.trim());

    if (validAddresses.length < 2) {
      setError('Please enter at least 2 addresses');
      setValidating(false);
      return;
    }

    if (useDepot && !depot.trim()) {
      setError('Please enter a depot address or uncheck "Use Depot"');
      setValidating(false);
      return;
    }

    try {
      // Geocode depot first if used
      if (useDepot && depot.trim()) {
        const depotResponse = await fetch(`${API_BASE}/geocode`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ address: depot })
        });

        const depotData = await depotResponse.json();

        if (depotData.status === 'error') {
          throw new Error(`Failed to geocode depot: ${depotData.message}`);
        }

        setValidatedDepot(depotData.result);
      }

      // Geocode all delivery stops
      const response = await fetch(`${API_BASE}/geocode_batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ addresses: validAddresses })
      });

      const data = await response.json();

      if (data.status === 'error') {
        throw new Error(data.message);
      }

      if (data.errors && data.errors.length > 0) {
        const errorMsg = `Failed to geocode: ${data.errors.map(e => e.address).join(', ')}`;
        throw new Error(errorMsg);
      }

      const validLocations = data.results.filter(r => r !== null);

      if (validLocations.length < 2) {
        throw new Error('Need at least 2 valid locations after geocoding');
      }

      setValidatedLocations(validLocations);

    } catch (err) {
      setError(err.message || 'Failed to validate addresses');
      setValidatedLocations([]);
      setValidatedDepot(null);
    } finally {
      setValidating(false);
    }
  };

  const optimizeRoute = async () => {
    if (validatedLocations.length < 2) {
      setError('Please validate addresses first');
      return;
    }

    if (useDepot && !validatedDepot) {
      setError('Please validate depot address first');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      // If using depot, prepend it to locations array
      const locationsToOptimize = useDepot
        ? [validatedDepot, ...validatedLocations]
        : validatedLocations;

      const response = await fetch(`${API_BASE}/optimize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          locations: locationsToOptimize,
          mode: 'distance',
          return_to_start: useDepot  // Return to depot if using depot mode
        })
      });

      const data = await response.json();

      if (data.status === 'error') {
        throw new Error(data.message);
      }

      setResults(data.results);
    } catch (err) {
      setError(err.message || 'Failed to optimize route');
    } finally {
      setLoading(false);
    }
  };

  const formatDistance = (meters) => {
    const miles = meters / 1609.34;
    return `${miles.toFixed(1)} mi`;
  };

  const formatTime = (seconds) => {
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    if (hours > 0) return `${hours}h ${mins}m`;
    return `${mins} min`;
  };

  const isAddressValidated = (index) => {
    const address = addresses[index]?.trim();
    if (!address) return false;
    return validatedLocations.some(loc =>
      loc.address.toLowerCase().includes(address.toLowerCase()) ||
      loc.formatted_address.toLowerCase().includes(address.toLowerCase())
    );
  };

  const isDepotValidated = () => {
    return useDepot && depot.trim() && validatedDepot !== null;
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 p-4 md:p-8">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="mb-8 text-center">
          <div className="flex items-center justify-center gap-3 mb-3">
            <div className="w-12 h-12 bg-blue-500/20 rounded-lg flex items-center justify-center">
              <Navigation className="w-6 h-6 text-blue-400" />
            </div>
            <h1 className="text-3xl font-bold text-white">Route Optimizer</h1>
          </div>
          <p className="text-slate-400">Isuzu BEV Fleet - Held-Karp TSP Solver</p>
          {useDepot && (
            <p className="text-green-400 text-sm mt-2">✓ Depot Mode: Vehicle returns to starting location</p>
          )}
        </div>

        <div className="grid md:grid-cols-2 gap-6">
          {/* Input Panel */}
          <div className="space-y-4">
            {/* Depot Section */}
            <div className="bg-slate-800 rounded-xl p-6 border border-slate-700">
              <div className="flex items-center justify-between mb-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useDepot}
                    onChange={(e) => {
                      setUseDepot(e.target.checked);
                      setValidatedDepot(null);
                      setValidatedLocations([]);
                      setResults(null);
                    }}
                    className="rounded border-slate-600 bg-slate-700 text-blue-500 focus:ring-blue-500"
                  />
                  <Home className="w-4 h-4 text-blue-400" />
                  <span className="text-sm font-medium text-slate-300">Use Depot (Return to Start)</span>
                </label>
              </div>

              {useDepot && (
                <div className="relative">
                  <input
                    type="text"
                    value={depot}
                    onChange={(e) => updateDepot(e.target.value)}
                    placeholder="Depot/Home address"
                    className={`w-full px-4 py-3 pr-10 bg-slate-700 border ${
                      isDepotValidated()
                        ? 'border-green-500'
                        : 'border-slate-600'
                    } rounded-lg text-white placeholder-slate-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent`}
                  />
                  {isDepotValidated() && (
                    <CheckCircle className="w-5 h-5 text-green-400 absolute right-3 top-1/2 -translate-y-1/2" />
                  )}
                </div>
              )}
            </div>

            {/* Delivery Stops Section */}
            <div className="bg-slate-800 rounded-xl p-6 border border-slate-700">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <MapPin className="w-5 h-5 text-blue-400" />
                  Delivery Stops
                </h2>
                <button
                  onClick={loadDemoAddresses}
                  className="flex items-center gap-1 px-3 py-1.5 bg-purple-600/20 hover:bg-purple-600/30 text-purple-400 text-sm rounded-lg transition-colors"
                >
                  <Download className="w-4 h-4" />
                  Load Demo
                </button>
              </div>

              <div className="space-y-3">
                {addresses.map((address, index) => (
                  <div key={index} className="flex gap-2">
                    <div className="flex-1 relative">
                      <input
                        type="text"
                        value={address}
                        onChange={(e) => updateAddress(index, e.target.value)}
                        placeholder={`Stop ${index + 1} address`}
                        className={`w-full px-4 py-3 pr-10 bg-slate-700 border ${
                          isAddressValidated(index) && validatedLocations.length > 0
                            ? 'border-green-500'
                            : 'border-slate-600'
                        } rounded-lg text-white placeholder-slate-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent`}
                      />
                      {isAddressValidated(index) && validatedLocations.length > 0 && (
                        <CheckCircle className="w-5 h-5 text-green-400 absolute right-3 top-1/2 -translate-y-1/2" />
                      )}
                    </div>
                    {addresses.length > 2 && (
                      <button
                        onClick={() => removeAddress(index)}
                        className="px-3 py-3 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded-lg transition-colors"
                      >
                        <Trash2 className="w-5 h-5" />
                      </button>
                    )}
                  </div>
                ))}
              </div>

              <button
                onClick={addAddress}
                className="w-full mt-3 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition-colors flex items-center justify-center gap-2"
              >
                <Plus className="w-4 h-4" />
                Add Stop
              </button>
            </div>

            {/* Action Buttons */}
            <div className="space-y-2">
              <button
                onClick={validateAddresses}
                disabled={validating || addresses.filter(a => a.trim()).length < 2 || (useDepot && !depot.trim())}
                className="w-full py-3 bg-green-600 hover:bg-green-700 disabled:bg-slate-600 disabled:cursor-not-allowed text-white font-semibold rounded-xl transition-colors flex items-center justify-center gap-2"
              >
                {validating ? (
                  <>
                    <Loader2 className="w-5 h-5 animate-spin" />
                    Validating...
                  </>
                ) : (
                  <>
                    <CheckCircle className="w-5 h-5" />
                    Validate Addresses
                  </>
                )}
              </button>

              <button
                onClick={optimizeRoute}
                disabled={loading || validatedLocations.length < 2 || (useDepot && !validatedDepot)}
                className="w-full py-4 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-600 disabled:cursor-not-allowed text-white font-semibold rounded-xl transition-colors flex items-center justify-center gap-2"
              >
                {loading ? (
                  <>
                    <Loader2 className="w-5 h-5 animate-spin" />
                    Optimizing...
                  </>
                ) : (
                  <>
                    <Route className="w-5 h-5" />
                    Optimize Route
                  </>
                )}
              </button>
            </div>

            {error && (
              <div className="bg-red-500/10 border border-red-500/50 rounded-lg p-4 text-red-400 flex items-start gap-2">
                <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
            )}

            {validatedLocations.length > 0 && !results && (
              <div className="bg-green-500/10 border border-green-500/50 rounded-lg p-4 text-green-400 flex items-start gap-2">
                <CheckCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                <div>
                  <p>{validatedLocations.length} stop{validatedLocations.length > 1 ? 's' : ''} validated!</p>
                  {validatedDepot && <p className="text-sm mt-1">✓ Depot validated</p>}
                  <p className="text-sm mt-1">Click "Optimize Route" to continue.</p>
                </div>
              </div>
            )}
          </div>

          {/* Results Panel */}
          <div className="space-y-4">
            {results && results.optimal && (
              <>
                {/* Optimal Route */}
                <div className="bg-slate-800 rounded-xl p-6 border border-green-500/30">
                  <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                    <Route className="w-5 h-5 text-green-400" />
                    Optimal Route (Held-Karp)
                  </h2>

                  <div className="space-y-3 mb-4">
                    {results.optimal.ordered_locations.map((loc, i) => (
                      <div key={i} className="flex items-start gap-3">
                        <div className={`w-8 h-8 ${i === 0 && useDepot ? 'bg-blue-500/20 text-blue-400' : 'bg-green-500/20 text-green-400'} rounded-lg flex items-center justify-center flex-shrink-0 font-semibold text-sm`}>
                          {i === 0 && useDepot ? <Home className="w-4 h-4" /> : i + 1}
                        </div>
                        <div className="flex-1">
                          <p className="text-white text-sm">{loc.formatted_address}</p>
                          {i < results.optimal.legs.length && (
                            <p className={`text-slate-500 text-xs mt-1 ${results.optimal.legs[i].return_leg ? 'text-blue-400' : ''}`}>
                              {results.optimal.legs[i].return_leg ? '↩ Return to depot: ' : '↓ '}
                              {formatDistance(results.optimal.legs[i].distance_m)} · {formatTime(results.optimal.legs[i].time_s)}
                            </p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="border-t border-slate-700 pt-4 space-y-2 mb-4">
                    <div className="flex justify-between text-sm">
                      <span className="text-slate-400">Total Distance:</span>
                      <span className="text-white font-semibold">{formatDistance(results.optimal.total_distance_m)}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-slate-400">Total Time:</span>
                      <span className="text-white font-semibold">{formatTime(results.optimal.total_time_s)}</span>
                    </div>
                  </div>

                  <a
                    href={results.optimal.google_maps_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="w-full py-3 bg-green-600 hover:bg-green-700 text-white rounded-lg transition-colors flex items-center justify-center gap-2"
                  >
                    <Navigation className="w-4 h-4" />
                    Open Optimal Route in Google Maps
                  </a>
                </div>

                {/* Greedy Route - Prominent Display */}
                {results.greedy && (
                  <div className="bg-slate-800 rounded-xl p-6 border border-orange-500/30">
                    <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                      <Route className="w-5 h-5 text-orange-400" />
                      Greedy Route (Baseline)
                    </h2>

                    <div className="border-t border-slate-700 pt-4 space-y-2 mb-4">
                      <div className="flex justify-between text-sm">
                        <span className="text-slate-400">Total Distance:</span>
                        <span className="text-white font-semibold">{formatDistance(results.greedy.total_distance_m)}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-slate-400">Total Time:</span>
                        <span className="text-white font-semibold">{formatTime(results.greedy.total_time_s)}</span>
                      </div>
                    </div>

                    <a
                      href={results.greedy.google_maps_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="w-full py-3 bg-orange-600 hover:bg-orange-700 text-white rounded-lg transition-colors flex items-center justify-center gap-2"
                    >
                      <Navigation className="w-4 h-4" />
                      Open Greedy Route in Google Maps
                    </a>
                  </div>
                )}

                {/* Comparison */}
                {results.greedy && results.comparison && (
                  <div className="bg-slate-800 rounded-xl p-6 border border-slate-700">
                    <h2 className="text-lg font-semibold text-white mb-4">
                      Savings: Optimal vs Greedy
                    </h2>

                    <div className="space-y-2 text-sm">
                      <div className="flex justify-between">
                        <span className="text-slate-400">Distance Saved:</span>
                        <span className="text-green-400 font-semibold">
                          {formatDistance(results.comparison.distance_saved_m)} ({results.comparison.distance_saved_percent.toFixed(1)}%)
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-slate-400">Time Saved:</span>
                        <span className="text-green-400 font-semibold">
                          {formatTime(results.comparison.time_saved_s)} ({results.comparison.time_saved_percent.toFixed(1)}%)
                        </span>
                      </div>
                    </div>
                  </div>
                )}
              </>
            )}

            {!results && !error && (
              <div className="bg-slate-800 rounded-xl p-8 border border-slate-700 text-center">
                <div className="w-16 h-16 bg-slate-700 rounded-full flex items-center justify-center mx-auto mb-4">
                  <Route className="w-8 h-8 text-slate-500" />
                </div>
                <p className="text-slate-400 mb-2">
                  Enter addresses, validate, then optimize
                </p>
                <p className="text-slate-500 text-sm">
                  Step 1: Validate → Step 2: Optimize
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default RouteOptimizer;
