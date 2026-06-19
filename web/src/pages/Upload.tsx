import { useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Upload, FileText, CheckCircle, Loader2, X } from 'lucide-react'
import Layout from '../components/Layout'
import ErrorBox from '../components/ErrorBox'
import { ingestCsv, pollJob, type IngestJob, ServiceWarmingError } from '../lib/api'

type Phase = 'idle' | 'uploading' | 'processing' | 'done' | 'error'

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [job, setJob] = useState<IngestJob | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [isDragOver, setIsDragOver] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  function handleFiles(files: FileList | null) {
    if (!files?.length) return
    const f = files[0]
    if (!f.name.endsWith('.csv') && f.type !== 'text/csv') {
      setError(new Error('Please upload a CSV file (.csv)'))
      return
    }
    setFile(f)
    setError(null)
  }

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(true)
  }, [])
  const onDragLeave = useCallback(() => setIsDragOver(false), [])
  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    handleFiles(e.dataTransfer.files)
  }, [])

  async function startUpload() {
    if (!file) return
    setPhase('uploading')
    setError(null)
    try {
      const newJob = await ingestCsv(file)
      setJob(newJob)
      setPhase('processing')
      await pollUntilDone(newJob.job_id)
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Upload failed'))
      setPhase('error')
    }
  }

  async function pollUntilDone(jobId: string) {
    let attempts = 0
    const MAX = 120 // 4 minutes
    while (attempts < MAX) {
      await new Promise(r => setTimeout(r, 2000))
      try {
        const updated = await pollJob(jobId)
        setJob(updated)
        if (updated.status === 'done') {
          setPhase('done')
          setTimeout(() => navigate('/dashboard'), 1500)
          return
        }
        if (updated.status === 'failed') {
          throw new Error(`Processing failed: ${updated.failed} of ${updated.total} reviews could not be processed.`)
        }
      } catch (err) {
        if (err instanceof ServiceWarmingError) continue // tolerate transient 503 during polling
        throw err
      }
      attempts++
    }
    throw new Error('Processing is taking longer than expected. Check your dashboard in a few minutes.')
  }

  function reset() {
    setFile(null)
    setPhase('idle')
    setJob(null)
    setError(null)
  }

  return (
    <Layout active="upload">
      <div className="max-w-xl">
        <h1 className="font-display text-2xl text-charcoal mb-1">Upload your reviews</h1>
        <p className="text-sm text-charcoal-light font-sans mb-8">
          Export your customer reviews as CSV and drop them here. We'll extract structured insights automatically.
        </p>

        {/* Drop zone */}
        {phase === 'idle' && (
          <>
            <div
              onClick={() => fileRef.current?.click()}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              className={`relative cursor-pointer rounded-xl border-2 border-dashed p-10 text-center transition-all ${
                isDragOver
                  ? 'border-green bg-green-light'
                  : 'border-gray-200 hover:border-green/50 hover:bg-green-light/30'
              }`}
            >
              <input
                ref={fileRef}
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                onChange={e => handleFiles(e.target.files)}
              />
              {file ? (
                <div className="flex items-center justify-center gap-3">
                  <FileText size={24} className="text-green" />
                  <div className="text-left">
                    <p className="font-sans font-medium text-charcoal text-sm">{file.name}</p>
                    <p className="font-sans text-xs text-charcoal-light mt-0.5">
                      {(file.size / 1024).toFixed(0)} KB · Ready to upload
                    </p>
                  </div>
                  <button
                    onClick={e => { e.stopPropagation(); reset() }}
                    className="ml-auto text-charcoal-light hover:text-charcoal"
                  >
                    <X size={16} />
                  </button>
                </div>
              ) : (
                <>
                  <Upload size={32} className="mx-auto text-charcoal-light/50 mb-3" />
                  <p className="font-sans font-medium text-charcoal text-sm">
                    Drop your reviews CSV here
                  </p>
                  <p className="font-sans text-xs text-charcoal-light mt-1">
                    or click to browse · max 500 rows · 5 MB
                  </p>
                </>
              )}
            </div>

            {error && (
              <div className="mt-4">
                <ErrorBox error={error} onRetry={reset} />
              </div>
            )}

            {file && (
              <button
                onClick={startUpload}
                className="mt-4 w-full flex items-center justify-center gap-2 bg-green hover:bg-green-muted text-white text-sm font-sans font-medium py-3 px-4 rounded-lg transition-colors"
              >
                <Upload size={15} /> Process {file.name}
              </button>
            )}

            <div className="mt-6 bg-white rounded-lg border border-gray-100 p-4 shadow-card">
              <p className="text-xs font-sans font-medium text-charcoal mb-2">CSV format tips</p>
              <ul className="text-xs font-sans text-charcoal-light space-y-1">
                <li>· One review per row · Text column is auto-detected</li>
                <li>· Optional: product name column for per-product insights</li>
                <li>· Works with any store — apparel, electronics, grocery, etc.</li>
              </ul>
            </div>
          </>
        )}

        {/* Processing state */}
        {(phase === 'uploading' || phase === 'processing') && job && (
          <div className="bg-white rounded-xl border border-gray-100 shadow-card p-8 text-center">
            <Loader2 size={32} className="animate-spin text-green mx-auto mb-4" />
            <h2 className="font-sans font-semibold text-charcoal text-base">
              {phase === 'uploading' ? 'Uploading your reviews...' : 'Processing your reviews'}
            </h2>
            <p className="text-sm text-charcoal-light font-sans mt-1">
              {phase === 'processing' && job.total > 0
                ? `${job.processed} of ${job.total} reviews done`
                : 'Hang tight — this usually takes under a minute.'}
            </p>
            {phase === 'processing' && job.total > 0 && (
              <div className="mt-4 bg-gray-100 rounded-full h-2 overflow-hidden">
                <div
                  className="bg-green h-full rounded-full transition-all duration-500"
                  style={{ width: `${Math.round((job.processed / job.total) * 100)}%` }}
                />
              </div>
            )}
          </div>
        )}

        {phase === 'uploading' && !job && (
          <div className="bg-white rounded-xl border border-gray-100 shadow-card p-8 text-center">
            <Loader2 size={32} className="animate-spin text-green mx-auto mb-4" />
            <p className="text-sm text-charcoal-light font-sans">Uploading...</p>
          </div>
        )}

        {/* Done state */}
        {phase === 'done' && (
          <div className="bg-white rounded-xl border border-gray-100 shadow-card p-8 text-center">
            <CheckCircle size={32} className="text-green mx-auto mb-4" />
            <h2 className="font-sans font-semibold text-charcoal text-base">Done!</h2>
            <p className="text-sm text-charcoal-light font-sans mt-1">
              {job?.processed} reviews processed. Taking you to your dashboard...
            </p>
          </div>
        )}

        {/* Error state */}
        {phase === 'error' && error && (
          <div className="space-y-4">
            <ErrorBox error={error} onRetry={reset} />
            <button
              onClick={reset}
              className="text-sm font-sans text-green hover:text-green-muted underline underline-offset-2 transition-colors"
            >
              Try a different file
            </button>
          </div>
        )}
      </div>
    </Layout>
  )
}
