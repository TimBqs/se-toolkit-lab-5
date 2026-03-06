import {
	BarElement,
	CategoryScale,
	Chart as ChartJS,
	Legend,
	LinearScale,
	LineElement,
	PointElement,
	Title,
	Tooltip,
} from 'chart.js'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Bar, Line } from 'react-chartjs-2'

ChartJS.register(
	CategoryScale,
	LinearScale,
	BarElement,
	LineElement,
	PointElement,
	Title,
	Tooltip,
	Legend,
)

const STORAGE_KEY = 'api_key'

// API Response types
interface ScoreBucket {
	bucket: string
	count: number
}

interface TimelineEntry {
	date: string
	submissions: number
}

interface PassRateEntry {
	task: string
	avg_score: number
	attempts: number
}

interface LabItem {
	id: number
	type: 'lab' | 'task'
	title: string
	parent_id: number | null
}

// Fetch state type
type FetchState<T> =
	| { status: 'idle' }
	| { status: 'loading' }
	| { status: 'success'; data: T }
	| { status: 'error'; message: string }

function Dashboard() {
	const [token] = useState(() => localStorage.getItem(STORAGE_KEY) ?? '')
	const [labs, setLabs] = useState<LabItem[]>([])
	const [selectedLab, setSelectedLab] = useState<string>('lab-04')

	const [scoresState, setScoresState] = useState<FetchState<ScoreBucket[]>>({ status: 'idle' })
	const [timelineState, setTimelineState] = useState<FetchState<TimelineEntry[]>>({
		status: 'idle',
	})
	const [passRatesState, setPassRatesState] = useState<FetchState<PassRateEntry[]>>({
		status: 'idle',
	})

	const hasInitializedRef = useRef(false)

	// Fetch analytics data
	const fetchAnalytics = useCallback(async (labId: string, authToken: string) => {
		const fetchWithAuth = async <T,>(url: string): Promise<T> => {
			const res = await fetch(url, {
				headers: { Authorization: `Bearer ${authToken}` },
			})
			if (!res.ok) throw new Error(`HTTP ${res.status}`)
			return res.json() as Promise<T>
		}

		// Fetch scores
		setScoresState({ status: 'loading' })
		fetchWithAuth<ScoreBucket[]>(`/analytics/scores?lab=${labId}`)
			.then(data => setScoresState({ status: 'success', data }))
			.catch((err: Error) => setScoresState({ status: 'error', message: err.message }))

		// Fetch timeline
		setTimelineState({ status: 'loading' })
		fetchWithAuth<TimelineEntry[]>(`/analytics/timeline?lab=${labId}`)
			.then(data => setTimelineState({ status: 'success', data }))
			.catch((err: Error) => setTimelineState({ status: 'error', message: err.message }))

		// Fetch pass rates
		setPassRatesState({ status: 'loading' })
		fetchWithAuth<PassRateEntry[]>(`/analytics/pass-rates?lab=${labId}`)
			.then(data => setPassRatesState({ status: 'success', data }))
			.catch((err: Error) => setPassRatesState({ status: 'error', message: err.message }))
	}, [])

	// Fetch labs on mount
	useEffect(() => {
		if (!token) return

		fetch('/items/', {
			headers: { Authorization: `Bearer ${token}` },
		})
			.then(res => {
				if (!res.ok) throw new Error(`HTTP ${res.status}`)
				return res.json() as Promise<LabItem[]>
			})
			.then(data => {
				setLabs(data)
				// Auto-select first lab if available
				const firstLab = data.find(item => item.type === 'lab')
				if (firstLab) {
					// Extract lab ID from title (e.g., "Lab 04 — Testing" -> "lab-04")
					const match = firstLab.title.match(/Lab (\d+)/i)
					if (match) {
						const labId = `lab-${match[1].padStart(2, '0')}`
						setSelectedLab(labId)
						// Mark as initialized and fetch analytics
						hasInitializedRef.current = true
						fetchAnalytics(labId, token)
					}
				}
			})
			.catch((err: Error) => {
				console.error('Failed to fetch labs:', err)
			})
	}, [token, fetchAnalytics])

	// Fetch analytics when lab changes (only for user-initiated changes after initial load)
	useEffect(() => {
		if (!token || !selectedLab || !hasInitializedRef.current) return

		// eslint-disable-next-line react-hooks/set-state-in-effect -- Data fetching is a legitimate use case
		fetchAnalytics(selectedLab, token)
	}, [selectedLab, token, fetchAnalytics])

	// Prepare chart data for scores
	const scoresChartData =
		scoresState.status === 'success'
			? {
					labels: scoresState.data.map(b => b.bucket),
					datasets: [
						{
							label: 'Number of Students',
							data: scoresState.data.map(b => b.count),
							backgroundColor: [
								'rgba(255, 99, 132, 0.6)',
								'rgba(255, 159, 64, 0.6)',
								'rgba(75, 192, 192, 0.6)',
								'rgba(54, 162, 235, 0.6)',
							],
							borderColor: [
								'rgb(255, 99, 132)',
								'rgb(255, 159, 64)',
								'rgb(75, 192, 192)',
								'rgb(54, 162, 235)',
							],
							borderWidth: 1,
						},
					],
				}
			: null

	// Prepare chart data for timeline
	const timelineChartData =
		timelineState.status === 'success'
			? {
					labels: timelineState.data.map(d => d.date),
					datasets: [
						{
							label: 'Submissions',
							data: timelineState.data.map(d => d.submissions),
							borderColor: 'rgb(75, 192, 192)',
							backgroundColor: 'rgba(75, 192, 192, 0.5)',
							tension: 0.1,
						},
					],
				}
			: null

	const commonChartOptions = {
		responsive: true,
		plugins: {
			legend: {
				position: 'top' as const,
			},
		},
	}

	if (!token) {
		return <div>Please log in to view the dashboard.</div>
	}

	// Extract unique lab IDs for dropdown
	const uniqueLabs = labs
		.filter(item => item.type === 'lab')
		.map(lab => {
			const match = lab.title.match(/Lab (\d+)/i)
			return match ? `lab-${match[1].padStart(2, '0')}` : ''
		})
		.filter(id => id !== '')

	return (
		<div className='dashboard'>
			{/* <h1>Dashboard</h1> */}

			{uniqueLabs.length === 0 ? (
				<div className='lab-selector'>
					<p>No labs available. Please run the ETL pipeline first:</p>
					<code>POST /pipeline/sync</code>
				</div>
			) : (
				<>
					<div className='lab-selector'>
						<label htmlFor='lab-select'>Select Lab: </label>
						<select
							id='lab-select'
							value={selectedLab}
							onChange={e => setSelectedLab(e.target.value)}
						>
							{uniqueLabs.map(labId => (
								<option key={labId} value={labId}>
									{labId.toUpperCase()}
								</option>
							))}
						</select>
					</div>

					{!selectedLab && <p>Please select a lab to view analytics.</p>}
				</>
			)}

			{selectedLab && (
				<>
					{/* Score Distribution Chart */}
					<section className='chart-section'>
						<h2>Score Distribution</h2>
						{scoresState.status === 'loading' && <p>Loading...</p>}
						{scoresState.status === 'error' && <p>Error: {scoresState.message}</p>}
						{scoresState.status === 'success' && scoresChartData && (
							<Bar data={scoresChartData} options={commonChartOptions} />
						)}
					</section>

					{/* Timeline Chart */}
					<section className='chart-section'>
						<h2>Submissions Timeline</h2>
						{timelineState.status === 'loading' && <p>Loading...</p>}
						{timelineState.status === 'error' && <p>Error: {timelineState.message}</p>}
						{timelineState.status === 'success' && timelineChartData && (
							<Line data={timelineChartData} options={commonChartOptions} />
						)}
					</section>

					{/* Pass Rates Table */}
					<section className='table-section'>
						<h2>Pass Rates by Task</h2>
						{passRatesState.status === 'loading' && <p>Loading...</p>}
						{passRatesState.status === 'error' && <p>Error: {passRatesState.message}</p>}
						{passRatesState.status === 'success' && (
							<table>
								<thead>
									<tr>
										<th>Task</th>
										<th>Avg Score</th>
										<th>Attempts</th>
									</tr>
								</thead>
								<tbody>
									{passRatesState.data.map((entry, index) => (
										<tr key={index}>
											<td>{entry.task}</td>
											<td>{entry.avg_score.toFixed(1)}</td>
											<td>{entry.attempts}</td>
										</tr>
									))}
								</tbody>
							</table>
						)}
					</section>
				</>
			)}
		</div>
	)
}

export default Dashboard
